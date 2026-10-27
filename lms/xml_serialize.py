"""Write ElementTree XML without leaving a prefix from this writer behind.

``ElementTree`` keeps namespace prefixes in one process-wide table.
``register_namespace`` changes that table. The lock in this module covers
only ``tostring_with_default_namespace``: the register-and-write inside
that function. It does not freeze the table against another thread that
calls ``register_namespace`` or ``tostring`` directly. SVG sanitising
writes its own bytes and does not use this lock.
"""

from __future__ import annotations

import threading
import xml.etree.ElementTree as ET

_namespace_lock = threading.Lock()


def tostring_with_default_namespace(elem: ET.Element, namespace: str) -> bytes:
    """Serialise ``elem`` with ``namespace`` as the default xmlns.

    The prefix table is saved and restored before this function releases
    the lock. The lock does not cover other threads that call
    ``register_namespace`` or ``tostring`` on their own.

    Args:
        elem: Element to write. Attributes stay in no namespace.
        namespace: URI emitted as ``xmlns``.

    Returns:
        UTF-8 XML bytes, including an XML declaration.
    """
    with _namespace_lock:
        saved = dict(ET._namespace_map)
        try:
            ET.register_namespace("", namespace)
            return ET.tostring(elem, encoding="utf-8", xml_declaration=True)
        finally:
            ET._namespace_map.clear()
            ET._namespace_map.update(saved)
