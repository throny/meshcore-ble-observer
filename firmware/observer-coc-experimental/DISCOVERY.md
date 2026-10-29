# CoC discovery descriptor

The host discovers the channel settings through service UUID
`1b5f6f02-6b56-485e-a350-613c4a5dc210` and its read-only characteristic
`1b5f6f07-6b56-485e-a350-613c4a5dc210`. The read requires
`SECMODE_ENC_WITH_MITM` and is additionally authorized by the existing observer
connected/bonded/secured/enrolled-peer check. This is not a pairing mechanism.

The characteristic value is exactly 8 bytes, little-endian where noted:

| Offset | Size | Meaning |
|---:|---:|---|
| 0 | 1 | Descriptor version, currently `1` |
| 1 | 1 | Configured channel count, `1` or `2` |
| 2 | 2 | Data LE PSM, `0x0080` (`80 00`) |
| 4 | 2 | Control LE PSM, `0x0081` (`81 00`) for two channels; zero for one |
| 6 | 2 | Maximum SDU, `558` (`2e 02`) |

The release uses two independent LE credit-based channels, one for data and one
for control. Clients must validate the version, channel count, PSMs and maximum
SDU before connecting. Discovery does not enroll a peer or prove that channel
setup will succeed.
