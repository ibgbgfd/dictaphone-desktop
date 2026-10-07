import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


EXAMPLE_SESSION_PAYLOAD = {
    "id": "совещание_312",
    "title": "Обсуждение архитектуры проекта",
    "date": "28/09/2026 12:15",
    "participants": [
        "Иванов И.И.",
        "Петров П.П."
    ],
    "agenda": [
        {
            "question": "Утверждение ТЗ на разработку аудиорекордера",
            "assignee": "Сидоров С.С.",
            "position": "Ведущий программист",
            "department": "Отдел разработки ПО"
        }
    ]
}


def validate_session_data(data: Any) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]], List[str]]:
    """
    Валидирует и нормализует JSON-пакет сессии совещания.
    
    Проверяет:
    1. Корневой элемент должен быть объектом (dict).
    2. Объект не должен быть пустым {}.
    3. Наличие и непустоту 'id' (идентификатор сессии/конференции).
    4. Наличие и непустоту 'title' (название конференции/совещания).
       Поддерживает дружелюбные синонимы ('conference', 'name', 'topic').
    5. Корректность типов полей (не допускает dict/list в id и title).
    6. Безопасную нормализацию 'date', 'participants' и 'agenda'.

    Возвращает:
        (is_valid, error_message, normalized_dict, missing_fields)
    """
    if not isinstance(data, dict):
        type_name = type(data).__name__
        msg = f"Корневой элемент JSON должен быть объектом {{...}}, а получен: {type_name}."
        return False, msg, None, []

    if not data:
        msg = "Передан пустой JSON объект {}. Заполните обязательные поля: 'id' (идентификатор) и 'title' (название конференции/совещания)."
        return False, msg, None, ["id", "title"]

    missing_fields: List[str] = []

    # 1. Проверка идентификатора 'id'
    raw_id = data.get("id")
    if raw_id is None:
        # Проверяем возможные синонимы
        raw_id = data.get("session_id") or data.get("meeting_id")

    id_str = ""
    if raw_id is None:
        missing_fields.append("id")
    elif isinstance(raw_id, (dict, list)):
        return False, f"Поле 'id' должно быть строковым или числовым идентификатором, а не {type(raw_id).__name__}.", None, []
    else:
        id_str = str(raw_id).strip()
        if not id_str:
            missing_fields.append("id")

    # 2. Проверка названия конференции / темы совещания 'title'
    raw_title = data.get("title")
    if raw_title is None or (isinstance(raw_title, str) and not raw_title.strip()):
        # Проверяем синонимы: conference, conference_name, name, topic, subject
        for alias in ("conference", "conference_name", "name", "topic", "subject"):
            val = data.get(alias)
            if val is not None and str(val).strip():
                raw_title = val
                break

    title_str = ""
    if raw_title is None:
        missing_fields.append("title")
    elif isinstance(raw_title, (dict, list)):
        return False, f"Поле 'title' (название конференции) должно быть строкой, а не {type(raw_title).__name__}.", None, []
    else:
        title_str = str(raw_title).strip()
        if not title_str:
            missing_fields.append("title")

    # Если отсутствуют обязательные поля
    if missing_fields:
        fields_desc = []
        for f in missing_fields:
            if f == "id":
                fields_desc.append("'id' (идентификатор конференции)")
            elif f == "title":
                fields_desc.append("'title' (название конференции/совещания)")
            else:
                fields_desc.append(f"'{f}'")

        msg = (
            f"Отсутствуют обязательные поля в JSON пакете: {', '.join(fields_desc)}. "
            f"Оба поля обязательны и не могут быть пустыми."
        )
        return False, msg, None, missing_fields

    # 3. Нормализация даты
    raw_date = data.get("date")
    if not raw_date or not str(raw_date).strip():
        date_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    else:
        date_str = str(raw_date).strip()

    # 4. Нормализация участников (participants)
    raw_participants = data.get("participants", [])
    normalized_participants: List[str] = []

    if isinstance(raw_participants, list):
        for item in raw_participants:
            if isinstance(item, dict):
                p_name = item.get("name") or item.get("fio") or item.get("fullName") or str(item)
                if str(p_name).strip():
                    normalized_participants.append(str(p_name).strip())
            elif item is not None and str(item).strip():
                normalized_participants.append(str(item).strip())
    elif isinstance(raw_participants, str):
        for p in raw_participants.split(","):
            if p.strip():
                normalized_participants.append(p.strip())

    # 5. Нормализация повестки дня (agenda)
    raw_agenda = data.get("agenda", [])
    normalized_agenda: List[Dict[str, str]] = []

    if isinstance(raw_agenda, list):
        for item in raw_agenda:
            if isinstance(item, dict):
                normalized_agenda.append({
                    "question": str(item.get("question") or item.get("title") or item.get("topic") or "").strip(),
                    "assignee": str(item.get("assignee") or item.get("speaker") or item.get("author") or "").strip(),
                    "position": str(item.get("position") or "").strip(),
                    "department": str(item.get("department") or "").strip()
                })
            elif isinstance(item, str) and item.strip():
                normalized_agenda.append({
                    "question": item.strip(),
                    "assignee": "",
                    "position": "",
                    "department": ""
                })
    elif isinstance(raw_agenda, str) and raw_agenda.strip():
        normalized_agenda.append({
            "question": raw_agenda.strip(),
            "assignee": "",
            "position": "",
            "department": ""
        })

    # Сборка итогового нормализованного объекта
    normalized: Dict[str, Any] = {
        "id": id_str,
        "title": title_str,
        "date": date_str,
        "participants": normalized_participants,
        "agenda": normalized_agenda
    }

    # Сохраняем дополнительные пользовательские поля без изменений
    for k, v in data.items():
        if k not in normalized:
            normalized[k] = v

    return True, None, normalized, []
