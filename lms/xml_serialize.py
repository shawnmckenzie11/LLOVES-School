"""Write ElementTree XML without leaving a process-wide namespace prefix behind.

``ElementTree`` keeps namespace prefixes in one process-wide table.
``register_namespace`` changes that table. SVG sanitising writes its own
bytes and does not touch the table. Manifest and slide writers still need
ElementTree, so they share the lock in this module for the whole
register-and-write.
"""

from __future__ import annotations

import threading
import xml.etree.ElementTree as ET

_namespace_lock = threading.Lock()


def tostring_with_default_namespace(elem: ET.Element, namespace: str) -> bytes:
    """Serialise ``elem`` with ``namespace`` as the default xmlns.

    The process-wide prefix table is saved and restored before the lock
    is released. A caller that does not use this function cannot observe
    a half-updated table from these writers.

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
