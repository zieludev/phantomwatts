"""Names, addresses and the port a trainer uses unless told otherwise."""

TRAINER_NAME = "PhantomBike"

# A static random address: its two top bits are set (Core Specification Vol 6, Part B, 1.3.2.1).
TRAINER_ADDRESS = "C0:FF:EE:00:00:01"

# The bridge of dlech/windows-vhci-driver connects to port 6402 by default, and its example
# controller reports this public address to Windows.
ATTACH = "tcp-server:127.0.0.1:6402"
CONTROLLER_ADDRESS = "F0:F1:F2:F3:F4:F5"
