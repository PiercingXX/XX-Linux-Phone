"""D-Bus contract for io.piercingxx.Captions1 (pure, no gi import).

The service implementation (lane 2) owns a real Gio connection; this
module pins the names, signatures, and validation so shell, tests, and
service can never drift apart.

Gio D-Bus replies are always a TUPLE of out-args: a GetAll-style reply
.unpack() yields ({...},), never {...}. Use unpack_single() on this
module's client side.
"""
from __future__ import annotations

BUS_NAME = "io.piercingxx.Captions1"
OBJECT_PATH = "/io/piercingxx/Captions1"
INTERFACE = "io.piercingxx.Captions1"

SOURCES = ("playback", "call", "mic")

INTROSPECTION_XML = """\
<node name="/io/piercingxx/Captions1">
  <interface name="io.piercingxx.Captions1">
    <method name="Start">
      <arg name="source" type="s" direction="in"/>
    </method>
    <method name="Stop">
    </method>
    <method name="ListTranscripts">
      <arg name="transcripts" type="a(ssxx)" direction="out"/>
    </method>
    <method name="GetTranscript">
      <arg name="id" type="s" direction="in"/>
      <arg name="peer" type="s" direction="out"/>
      <arg name="started" type="x" direction="out"/>
      <arg name="ended" type="x" direction="out"/>
      <arg name="lines" type="a(sx)" direction="out"/>
    </method>
    <signal name="Text">
      <arg name="text" type="s"/>
      <arg name="final" type="b"/>
    </signal>
    <property name="Active" type="b" access="read"/>
    <property name="Source" type="s" access="read"/>
  </interface>
</node>
"""


ERR_NOT_PERMITTED = BUS_NAME + ".Error.NotPermitted"
ERR_NOT_FOUND = BUS_NAME + ".Error.NotFound"


class ContractError(ValueError):
    pass


def validate_source(source: object) -> str:
    if not isinstance(source, str) or source not in SOURCES:
        raise ContractError(f"source must be one of {SOURCES}")
    return source


def validate_text(text: object, final: object) -> tuple[str, bool]:
    if not isinstance(text, str) or not text:
        raise ContractError("text must be a non-empty string")
    if not isinstance(final, bool):
        raise ContractError("final must be a bool")
    return text, final


def unpack_single(unpacked: object) -> object:
    """First out-arg of a Gio D-Bus reply's unpacked tuple."""
    if not isinstance(unpacked, tuple) or not unpacked:
        raise ContractError("D-Bus reply unpacks to a non-empty tuple")
    return unpacked[0]
