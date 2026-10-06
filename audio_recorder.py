import os
import sys
import time
import queue
import threading
import traceback
from pathlib import Path
from typing import Optional, Callable, List, Dict, Any

import numpy as np

try:
    import sounddevice as sd
except ImportError:
    sd = None

try:
    import av
except ImportError:
    av = None


class AudioRecorder:
    def __init__(
        self,
        sample_rate: int = 48000,
        channels: int = 1,
        codec_name: str = "libopus",
        bitrate: int = 64000,
        device_index: Optional[int] = None,
        on_level_callback: Optional[Callable[[float], None]] = None,
        on_error_callback: Optional[Callable[[str], None]] = None,
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.codec_name = codec_name
        self.bitrate = bitrate
        self.device_index = device_index if (device_index is not None and device_index >= 0) else None
        
        self.on_level = on_level_callback
        self.on_error = on_error_callback

        self.is_recording = False
        self.is_paused = False
        
        self._stream: Optional[sd.InputStream] = None
        self._container: Optional[Any] = None
        self._av_stream: Optional[Any] = None
        self._record_thread: Optional[threading.Thread] = None
        self._audio_queue: queue.Queue = queue.Queue(maxsize=300)
        self._stop_event = threading.Event()
        
        self.start_time: float = 0.0
        self.elapsed_time: float = 0.0
        self.current_filepath: Optional[str] = None
        self.total_samples_written: int = 0

    @staticmethod
    def get_input_devices() -> List[Dict[str, Any]]:
        """Returns clean list of available audio input devices with proper UTF-8 Cyrillic decoding."""
        if sd is None:
            return []
        
        devices = []
        # Always provide the system default microphone first
        devices.append({
            'index': -1,
            'name': 'По умолчанию (системный микрофон)',
            'channels': 1,
            'is_default': True
        })

        try:
            all_devs = sd.query_devices()
            host_apis = sd.query_hostapis()
            
            # Identify WASAPI host API on Windows
            wasapi_idx = None
            if sys.platform == "win32":
                for i, h in enumerate(host_apis):
                    if "wasapi" in h.get("name", "").lower():
                        wasapi_idx = i
                        break

            candidates = []
            for idx, dev in enumerate(all_devs):
                if dev.get('max_input_channels', 0) <= 0:
                    continue

                host_id = dev.get('hostapi', 0)
                host_name = host_apis[host_id].get('name', '') if host_id < len(host_apis) else ''

                # On Windows, skip raw kernel streaming (WDM-KS) and legacy DirectSound duplicates
                if sys.platform == "win32":
                    h_lower = host_name.lower()
                    if "wdm-ks" in h_lower or "directsound" in h_lower:
                        continue

                # Extract raw bytes from PortAudio C struct and decode UTF-8 first
                raw_bytes = None
                if hasattr(sd, '_ffi') and hasattr(sd, '_lib'):
                    try:
                        pa_info = sd._lib.Pa_GetDeviceInfo(idx)
                        if pa_info and pa_info.name:
                            raw_bytes = sd._ffi.string(pa_info.name)
                    except Exception:
                        pass

                if raw_bytes:
                    try:
                        name = raw_bytes.decode('utf-8')
                    except UnicodeDecodeError:
                        name = raw_bytes.decode('cp1251', errors='replace')
                else:
                    name = dev.get('name', '')
                    # Check for accidental mojibake
                    if any(ch in name for ch in ['Р', 'С', 'Ѓ', '‚']):
                        try:
                            name = name.encode('cp1251').decode('utf-8')
                        except Exception:
                            pass

                name = name.strip()
                # Skip driver path artifacts or loopback outputs
                if '@system32' in name.lower() or 'output with hap' in name.lower() or '2nd output' in name.lower():
                    continue

                # Filter out redundant "Переназначение звуковых устр. - Input" alias on Windows
                if 'переназначение' in name.lower() or 'первичный драйвер' in name.lower():
                    continue

                candidates.append({
                    'index': idx,
                    'name': name,
                    'host_id': host_id,
                    'is_wasapi': (host_id == wasapi_idx)
                })

            # If WASAPI devices exist on Windows, prefer them to avoid MME duplicate latency
            if sys.platform == "win32" and wasapi_idx is not None:
                wasapi_candidates = [c for c in candidates if c['is_wasapi']]
                chosen = wasapi_candidates if wasapi_candidates else candidates
            else:
                chosen = candidates

            seen_names = set()
            for c in chosen:
                clean_name = c['name']
                if clean_name not in seen_names:
                    seen_names.add(clean_name)
                    devices.append({
                        'index': c['index'],
                        'name': clean_name,
                        'channels': all_devs[c['index']].get('max_input_channels', 1),
                        'is_default': False
                    })

        except Exception as e:
            print(f"[AudioRecorder] Error querying devices: {e}")

        return devices

    def start_recording(self, output_filepath: str) -> bool:
        if self.is_recording:
            return False

        if sd is None:
            if self.on_error:
                self.on_error("Библиотека sounddevice не установлена!")
            return False

        if av is None:
            if self.on_error:
                self.on_error("Библиотека PyAV не установлена!")
            return False

        self.current_filepath = output_filepath
        Path(output_filepath).parent.mkdir(parents=True, exist_ok=True)

        try:
            layout_name = "mono" if self.channels == 1 else "stereo"
            
            # Container with live streaming options (resilient to sudden crash / kill)
            # cluster_time_limit: flush cluster to disk every 1000ms
            container_options = {
                "cluster_time_limit": "1000",
                "reserve_index_space": "0"
            }
            self._container = av.open(
                output_filepath,
                mode='w',
                format='webm',
                options=container_options
            )
            
            # Add audio stream
            try:
                self._av_stream = self._container.add_stream(
                    self.codec_name,
                    rate=self.sample_rate,
                    layout=layout_name
                )
            except Exception:
                # Fallback to generic opus
                self._av_stream = self._container.add_stream(
                    "opus",
                    rate=self.sample_rate,
                    layout=layout_name
                )

            self._av_stream.format = 'flt'
            self._av_stream.bit_rate = self.bitrate

            self.total_samples_written = 0
            self._audio_queue = queue.Queue(maxsize=300)
            self._stop_event.clear()
            self.is_recording = True
            self.is_paused = False
            self.start_time = time.time()
            self.elapsed_time = 0.0

            # Start background encoding worker thread
            self._record_thread = threading.Thread(target=self._encode_worker, daemon=True)
            self._record_thread.start()

            # Block size: 960 frames = 20ms at 48000 Hz, optimal for Opus
            block_size = int(self.sample_rate * 0.02)
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                device=self.device_index,
                dtype='float32',
                blocksize=block_size,
                callback=self._audio_callback
            )
            self._stream.start()
            return True

        except Exception as e:
            traceback.print_exc()
            self.cleanup()
            if self.on_error:
                self.on_error(f"Ошибка старта записи: {str(e)}")
            return False

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        """Called by sounddevice in high-priority audio callback."""
        if not self.is_recording or self.is_paused:
            return

        # Queue audio block for encoding
        try:
            self._audio_queue.put_nowait(indata.copy())
        except queue.Full:
            pass

    def _encode_worker(self) -> None:
        """Worker thread converting raw PCM chunks to Opus WebM frames."""
        buffer = []
        target_frame_size = int(self.sample_rate * 0.02)  # 20ms

        while not self._stop_event.is_set() or not self._audio_queue.empty():
            try:
                data = self._audio_queue.get(timeout=0.1)
                buffer.append(data)
            except queue.Empty:
                continue

            total_samples = sum(len(c) for c in buffer)
            if total_samples >= target_frame_size:
                combined = np.concatenate(buffer, axis=0)
                while len(combined) >= target_frame_size:
                    chunk = combined[:target_frame_size]
                    combined = combined[target_frame_size:]
                    self._write_chunk(chunk)

                buffer = [combined] if len(combined) > 0 else []

        # Write any trailing audio
        if buffer:
            combined = np.concatenate(buffer, axis=0)
            if len(combined) > 0:
                self._write_chunk(combined)

        # Flush encoder packets to container
        if self._av_stream and self._container:
            try:
                for packet in self._av_stream.encode():
                    self._container.mux(packet)
            except Exception as e:
                print(f"[AudioRecorder] Error flushing encoder: {e}")

    def _write_chunk(self, chunk: np.ndarray) -> None:
        if not self._container or not self._av_stream:
            return

        try:
            # Shape for av.AudioFrame: (channels, samples)
            if self.channels == 1:
                pcm_data = chunk.reshape(1, -1).astype(np.float32)
            else:
                pcm_data = chunk.T.astype(np.float32)

            layout_name = "mono" if self.channels == 1 else "stereo"
            frame = av.AudioFrame.from_ndarray(pcm_data, format='flt', layout=layout_name)
            frame.sample_rate = self.sample_rate
            frame.pts = self.total_samples_written
            self.total_samples_written += chunk.shape[0]

            for packet in self._av_stream.encode(frame):
                self._container.mux(packet)
        except Exception as e:
            print(f"[AudioRecorder] Chunk encoding error: {e}")

    def stop_recording(self) -> float:
        if not self.is_recording:
            return 0.0

        self.is_recording = False
        duration = time.time() - self.start_time
        self.elapsed_time = duration

        # Stop audio hardware input
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                print(f"[AudioRecorder] Error stopping input stream: {e}")
            self._stream = None

        # Stop worker thread and flush
        self._stop_event.set()
        if self._record_thread and self._record_thread.is_alive():
            self._record_thread.join(timeout=3.0)
        self._record_thread = None

        # Close container
        if self._container:
            try:
                self._container.close()
            except Exception as e:
                print(f"[AudioRecorder] Error closing container: {e}")
            self._container = None
            self._av_stream = None

        return duration

    def cleanup(self) -> None:
        self.is_recording = False
        self._stop_event.set()
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._container:
            try:
                self._container.close()
            except Exception:
                pass
            self._container = None
