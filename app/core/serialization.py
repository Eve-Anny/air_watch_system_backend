"""
Mongo documents use ObjectId for _id, which FastAPI's default JSON encoder can't serialize.
These helpers convert _id -> a string "id" field; every other field type (datetime, float, etc.)
serializes fine via FastAPI's default jsonable_encoder for dict responses without a response_model.
"""


def _to_out(doc: dict) -> dict:
    doc = dict(doc)
    if "_id" in doc:
        doc["id"] = str(doc.pop("_id"))
    return doc


def serialize_reading(doc: dict | None) -> dict | None:
    if not doc:
        return doc
    return _to_out(doc)


def serialize_alert(doc: dict | None) -> dict | None:
    if not doc:
        return doc
    return _to_out(doc)


def serialize_device(doc: dict | None) -> dict | None:
    if not doc:
        return doc
    doc = dict(doc)
    doc.pop("_id", None)  # devices are keyed by device_id, not exposed via Mongo's _id
    return doc
