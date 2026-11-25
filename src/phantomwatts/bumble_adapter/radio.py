"""Where the trainer's Bluetooth stack gets a controller from.

`VirtualRadio` puts the trainer on a virtual controller attached to an in-process link, Bumble's
stand-in for the air. A second virtual controller on that link can be offered to a host stack
outside this process through a Bumble transport spec: TCP for the Windows bridge, /dev/vhci for
Linux. `HciTransport` skips all that and drives a controller that already exists.
"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from typing import TypeAlias

from bumble import hci, ll
from bumble.controller import Controller
from bumble.core import PhysicalTransport
from bumble.link import LocalLink
from bumble.transport import open_transport
from bumble.transport.common import StreamPacketSource, Transport, TransportSink, TransportSource

from phantomwatts import defaults

TCP_SERVER_SCHEME = "tcp-server"
WINVHCI_SCHEME = "winvhci"
_ALL_INTERFACES = "_"


@dataclass(frozen=True)
class VirtualRadio:
    """The trainer on a simulated link, with no Bluetooth hardware anywhere.

    `expose` is a Bumble transport spec on which a virtual controller is offered to an
    external host stack; without it only centrals in this process can join. `windows` applies
    the controller shims of the winvhci package, which the Windows stack needs to come up.
    """

    expose: str | None = None
    controller_address: str = defaults.CONTROLLER_ADDRESS
    windows: bool = False


@dataclass(frozen=True)
class HciTransport:
    """The trainer's own stack on an existing controller, named by a Bumble transport spec."""

    spec: str


Radio: TypeAlias = VirtualRadio | HciTransport


class OwnAddressLink(LocalLink):
    """LocalLink that labels LE data with the address its connection was made from.

    Bumble's LocalLink labels every LE packet with the sender's random address. A host stack
    that connects from its public address, as Windows does, never sets one, so the receiving
    controller finds no connection for the packet and drops it without a word.
    """

    def send_acl_data(
        self,
        sender_controller: Controller,
        destination_address: hci.Address,
        transport: PhysicalTransport,
        data: bytes,
    ) -> None:
        connection = (
            sender_controller.le_connections.get(destination_address)
            if transport == PhysicalTransport.LE
            else None
        )
        receiver = self.find_le_controller(destination_address)
        if connection is None or receiver is None:
            super().send_acl_data(sender_controller, destination_address, transport, data)
            return
        asyncio.get_running_loop().call_soon(
            receiver.on_link_acl_data, connection.self_address, transport, data
        )


def _forget_host(controller: Controller) -> None:
    """End what a departed host left behind: its LE links, its scan, its pending connection.

    Bumble's virtual controller keeps all of it when its host resets or goes away. Without
    this the trainer would stay connected to a ghost and never advertise again.
    """
    for connection in list(controller.le_connections.values()):
        connection.send_ll_control_pdu(ll.TerminateInd(hci.HCI_CONNECTION_TIMEOUT_ERROR))
        del controller.le_connections[connection.peer_address]
    controller.le_scan_enable = False
    controller.pending_le_connection = None


def _forgetting_host_on_reset(base: type[Controller]) -> type[Controller]:
    class ForgetsHostOnReset(base):
        def on_hci_reset_command(
            self, command: hci.HCI_SyncCommand[hci.HCI_StatusReturnParameters]
        ) -> hci.HCI_StatusReturnParameters:
            _forget_host(self)
            return super().on_hci_reset_command(command)

    return ForgetsHostOnReset


@dataclass(frozen=True)
class _Flavor:
    link: type[LocalLink]
    controller: type[Controller]
    prepare_host_controller: Callable[[Controller], None]


def _flavor(windows: bool) -> _Flavor:
    if not windows:
        return _Flavor(OwnAddressLink, _forgetting_host_on_reset(Controller), lambda _: None)
    try:
        from winvhci import bumble_compat
    except ImportError as error:
        raise RuntimeError(
            "the Windows controller shims come from the winvhci package of "
            "github.com/dlech/windows-vhci-driver, which is not installed"
        ) from error
    return _Flavor(
        bumble_compat.WindowsCompatLink,
        _forgetting_host_on_reset(bumble_compat.WindowsCompatController),
        bumble_compat.apply_dual_mode,
    )


class TcpHostTransport(Transport):
    """HCI in H4 framing over TCP, for one host stack at a time.

    Speaks what Bumble's tcp-server transport speaks, but that one is not used: it never closes
    its listening socket, carries half a packet from one connection into the next, and lets an
    old connection that closes late cut off its successor. Here a newer connection replaces
    the older one, and the owner hears when the host is gone.
    """

    def __init__(self) -> None:
        self._packets = StreamPacketSource()
        super().__init__(self._packets, self)
        self.on_host_lost: Callable[[], None] = lambda: None
        self._client: asyncio.BaseTransport | None = None
        self._server: asyncio.Server | None = None

    @property
    def port(self) -> int | None:
        if self._server is None or not self._server.sockets:
            return None
        port: int = self._server.sockets[0].getsockname()[1]
        return port

    async def listen(self, host: str | None, port: int) -> None:
        self._server = await asyncio.get_running_loop().create_server(
            lambda: _HostConnection(self), host, port
        )

    def on_packet(self, packet: bytes) -> None:
        if isinstance(self._client, asyncio.WriteTransport):
            self._client.write(packet)

    def host_arrived(self, client: asyncio.BaseTransport) -> None:
        if self._client is not None:
            self._client.close()
            self.on_host_lost()
        self._packets.parser.reset()
        self._client = client

    def host_left(self, client: asyncio.BaseTransport) -> None:
        if client is self._client:
            self._client = None
            self.on_host_lost()

    def host_sent(self, client: asyncio.BaseTransport, data: bytes) -> None:
        if client is self._client:
            self._packets.data_received(data)

    async def close(self) -> None:
        # Deliberately not super().close(): that closes the sink, and the sink is this object.
        if self._server is not None:
            self._server.close()
        if self._client is not None:
            self._client.close()
        if self._server is not None:
            await self._server.wait_closed()


class _HostConnection(asyncio.Protocol):
    def __init__(self, endpoint: TcpHostTransport) -> None:
        self._endpoint = endpoint
        self._transport: asyncio.BaseTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport
        self._endpoint.host_arrived(transport)

    def data_received(self, data: bytes) -> None:
        if self._transport is not None:
            self._endpoint.host_sent(self._transport, data)

    def connection_lost(self, exc: Exception | None) -> None:
        if self._transport is not None:
            self._endpoint.host_left(self._transport)


class Attachment:
    """An open radio: the HCI endpoints for the trainer's host, and a way onto the link."""

    def __init__(
        self,
        hci_source: TransportSource,
        hci_sink: TransportSink,
        *,
        tcp_port: int | None = None,
        link: LocalLink | None = None,
        flavor: _Flavor | None = None,
    ) -> None:
        self.hci_source = hci_source
        self.hci_sink = hci_sink
        self.tcp_port = tcp_port
        self._link = link
        self._flavor = flavor

    def add_controller(self, name: str, address: str) -> Controller:
        """Put another virtual controller on the link, for a host stack in this process."""
        if self._link is None or self._flavor is None:
            raise RuntimeError("only a virtual radio has a link that controllers can join")
        return self._flavor.controller(name, link=self._link, public_address=address)


@contextlib.asynccontextmanager
async def attach(radio: Radio, trainer_address: str) -> AsyncGenerator[Attachment, None]:
    if isinstance(radio, HciTransport):
        async with await open_transport(radio.spec) as transport:
            yield Attachment(transport.source, transport.sink)
        return

    flavor = _flavor(radio.windows)
    link = flavor.link()
    trainer = flavor.controller("trainer", link=link, public_address=trainer_address)
    if radio.expose is None:
        yield Attachment(trainer, trainer, link=link, flavor=flavor)
        return

    async with await _open_host_transport(radio.expose) as transport:
        host = flavor.controller(
            "host",
            host_source=transport.source,
            host_sink=transport.sink,
            link=link,
            public_address=radio.controller_address,
        )
        flavor.prepare_host_controller(host)
        tcp_port = None
        if isinstance(transport, TcpHostTransport):
            transport.on_host_lost = lambda: _forget_host(host)
            tcp_port = transport.port
        yield Attachment(trainer, trainer, tcp_port=tcp_port, link=link, flavor=flavor)


async def _open_host_transport(spec: str) -> Transport:
    scheme, _, parameters = spec.partition(":")
    if scheme == TCP_SERVER_SCHEME:
        host, _, port = parameters.rpartition(":")
        transport = TcpHostTransport()
        await transport.listen(None if host == _ALL_INTERFACES else host, int(port))
        return transport
    if scheme != WINVHCI_SCHEME:
        return await open_transport(spec)
    try:
        from winvhci.transport import open_winvhci_transport
    except ImportError as error:
        raise RuntimeError(
            "the winvhci transport needs the winvhci package of "
            "github.com/dlech/windows-vhci-driver, on Windows with its driver installed"
        ) from error
    return await open_winvhci_transport(parameters or None)
