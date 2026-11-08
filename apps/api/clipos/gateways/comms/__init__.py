from clipos.gateways.comms.contracts import (
    CommsCapabilities,
    CommsChannel,
    CommunicationsAdapter,
    DeliveryReceipt,
    OutboundMessage,
)
from clipos.gateways.comms.disabled import DisabledCommunicationsAdapter
from clipos.gateways.comms.errors import CommsAdapterError, UnsupportedCommsError
from clipos.gateways.comms.factory import CommsAdapterRegistry, build_comms_adapters
from clipos.gateways.comms.fake import FakeCommsAdapter

__all__ = [
    "CommsAdapterError",
    "CommsAdapterRegistry",
    "CommsCapabilities",
    "CommsChannel",
    "CommunicationsAdapter",
    "DeliveryReceipt",
    "DisabledCommunicationsAdapter",
    "FakeCommsAdapter",
    "OutboundMessage",
    "UnsupportedCommsError",
    "build_comms_adapters",
]
