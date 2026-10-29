# Enroll a Bluetooth host

Enrollment authorizes one Linux host to receive the repeater's logs and issue
the supported observer commands. It requires physical USB console access. Stop
the receiver before changing enrollment.

**Starting enrollment replaces the previously authorized host.** Do not run it
just to troubleshoot an existing connection.

1. Connect the repeater's USB console and keep it connected during enrollment.
2. Enable the observer, then open the 60-second pairing window:

   ```text
   observer enable
   observer enroll
   ```

3. On the intended Linux host, pair with the repeater using BlueZ (for example,
   `bluetoothctl`). Use an authenticated passkey-capable pairing agent, enter the
   six-digit passkey shown on the USB console, and mark that peer trusted. Do not
   accept a different nearby device or use unauthenticated pairing.
4. Confirm that the USB console reports `Observer BLE peer pinned; enrollment closed.`
   Check the state with:

   ```text
   observer enrollment status
   observer status
   ```

The pairing window closes on timeout or loss of USB console access. To close it
manually, use `observer enrollment close`. To remove the authorized host, use
`observer revoke`; to stop the observer, use `observer disable`.

Record the enrolled repeater's Bluetooth address for the host configuration,
then follow the [host setup guide](https://github.com/throny/meshcoretomqtt/blob/feature/repeater-observer-coc-20261029/docs/coc.md).
The host helper reconnects to an enrolled peer; it does not perform enrollment.
