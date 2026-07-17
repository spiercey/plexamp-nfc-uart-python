import time
import serial
import requests
from adafruit_pn532.uart import PN532_UART
from serial.tools import list_ports


PN532KILLER_VID_PID = (0x1A86, 0x55D3)


class PlexampPN532_UART(PN532_UART):
    # Don't crash on no-card polls
    def get_passive_target(self, timeout=1):
        response = self.process_response(
            0x4A,  # InListPassiveTarget
            response_length=64,
            timeout=timeout,
        )

        if not response or response[0] == 0:
            return None

        if response[0] != 1:
            raise RuntimeError(f"Unexpected target count: {response[0]}")

        uid_length = response[5]
        if uid_length > 7:
            raise RuntimeError("Found card with unexpectedly long UID!")

        return response[6 : 6 + uid_length]


class PN532Killer_UART(PlexampPN532_UART):
    @staticmethod
    def _crc16a(data):
        crc = 0x6363
        for value in data:
            value ^= crc & 0xFF
            value = (value ^ (value << 4)) & 0xFF
            crc = (crc >> 8) ^ (value << 8) ^ (value << 3) ^ (value >> 4)
        return (crc & 0xFFFF).to_bytes(2, byteorder="little")

    def ntag2xx_read_block(self, block_number):
        # PN532Killer acknowledges InDataExchange reads but doesn't return
        # data. Its supported raw-card path is InCommunicateThru with
        # the ISO14443A CRC appended to the MIFARE READ command.
        command = bytes((0x30, block_number & 0xFF))
        response = self.call_function(
            0x42,  # InCommunicateThru
            params=command + self._crc16a(command),
            response_length=19,  # status + 16 data bytes + 2 CRC bytes
        )

        if not response or response[0] != 0:
            return None

        # A MIFARE READ returns four pages. Keep the requested 4-byte page to
        # match the Adafruit ntag2xx_read_block API.
        return response[1:5]


# ----------------------------
# Helper: identify pn532killer boards using usb metadata
# ----------------------------
def is_pn532killer(port):
    product = port.product or ""
    description = port.description or ""
    return (
        "PN532Killer" in product
        or "PN532Killer" in description
        or (port.vid, port.pid) == PN532KILLER_VID_PID
    )


# ----------------------------
# Helper: find PN532 serial device
# ----------------------------
def find_uart_device():
    ports = list(list_ports.comports())

    # Prefer readers that can be positively identified
    for port in ports:
        if is_pn532killer(port):
            return port

    # Generic ttyACMs are only considered when no ttyUSBs exist
    for device_prefix in ("/dev/ttyUSB", "/dev/ttyACM"):
        candidates = sorted(
            (port for port in ports if port.device.startswith(device_prefix)),
            key=lambda port: port.device,
        )
        if candidates:
            return candidates[0]

    return None


# ----------------------------
# Helper: parse NDEF URI
# ----------------------------
def parse_ndef_uri(tag_data):
    uri_tnf_index = tag_data.find(b'\x55')  # Look for URI record
    if uri_tnf_index == -1 or uri_tnf_index + 2 >= len(tag_data):
        return None

    prefix_code = tag_data[uri_tnf_index + 1]
    raw_uri_bytes = tag_data[uri_tnf_index + 2:]

    prefix_map = {
        0x00: "", 0x01: "http://www.", 0x02: "https://www.",
        0x03: "http://", 0x04: "https://"
    }
    prefix = prefix_map.get(prefix_code, "")
    uri_path = raw_uri_bytes.decode("utf-8", errors="ignore")
    full_url = prefix + uri_path
    full_url = full_url.rstrip(" \n\r\x00\xfe\t")
    return full_url


# ----------------------------
# NFC reader setup
# ----------------------------
def connect_reader():
    while True:
        port = find_uart_device()
        if not port:
            print("Waiting for NFC reader...")
            time.sleep(2)
            continue

        device = port.device
        try:
            ser = serial.Serial(baudrate=115200, timeout=1)

            if is_pn532killer(port):
                # pySerial asserts DTR and RTS by default. Incorrect DTR/RTS
                # handling on a PN532Killer disrupts its configuration.
                ser.dtr = False
                ser.rts = False

            ser.port = device
            ser.open()

            reader_class = (
                PN532Killer_UART if is_pn532killer(port) else PlexampPN532_UART
            )
            pn532 = reader_class(ser, debug=False)
            pn532.SAM_configuration()
            print(f"NFC reader connected on {device}")
            return pn532
        except Exception as e:
            print(f"Failed to connect to {device}: {e}")
            time.sleep(2)


# ----------------------------
# Debounce & memory variables
# ----------------------------
last_uid = None
last_url = None
last_seen_time = 0
forget_after = 30  # seconds to "forget" a tag after removal


# ----------------------------
# Main
# ----------------------------
if __name__ == "__main__":
    pn532 = connect_reader()

    while True:
        try:
            uid = pn532.read_passive_target(timeout=0.5)
            now = time.time()

            if uid is None:
                # If no tag present for a while, forget the last one
                if last_uid and (now - last_seen_time) > forget_after:
                    print("Forgetting last tag (timeout reached).")
                    last_uid = None
                    last_url = None
                continue

            # Format UID
            uid_str = ''.join([f"{i:02x}" for i in uid])

            # If same UID still present and not expired, skip
            if uid == last_uid and (now - last_seen_time) <= forget_after:
                continue

            print(f"Tag detected! UID: {uid_str}")
            last_uid = uid
            last_seen_time = now

            # Read all relevant NTAG blocks
            tag_data = bytearray()
            for block in range(4, 50):
                try:
                    block_data = pn532.ntag2xx_read_block(block)
                    if block_data:
                        tag_data += block_data
                except Exception:
                    continue

            if not tag_data:
                print("No data read from tag.")
                continue

            # Parse URI
            full_url = parse_ndef_uri(tag_data)
            if not full_url:
                print("No valid NDEF URI found.")
                continue

            print(f"Parsed tag URL: {full_url}")

            # Classify and filter playable URLs
            kind = None
            if "metadata" in full_url:
                kind = "Track/Album"
            elif "playlists" in full_url:
                kind = "Playlist"
            elif "stations" in full_url:
                kind = "Station"
            elif "sections" in full_url:
                kind = "Library Section/Artist"

            if not kind:
                print("URL not recognized as playable — attempting playback.")
                kind = "Unknown"

            print(f"Detected tag type: {kind}")

            # Convert to local Plexamp URL
            local_url = full_url.replace("https://listen.plex.tv", "http://localhost:32500")
            local_url = local_url.replace("http://listen.plex.tv", "http://localhost:32500")
            print(f"Local Plexamp URL: {local_url}")

            # If same URL as before and still within active session, skip
            if local_url == last_url and (now - last_seen_time) <= forget_after:
                print("Same tag & URL already active — skipping trigger.")
                continue

            last_url = local_url

            # Trigger Plexamp playback
            try:
                response = requests.get(local_url)
                if response.ok:
                    print(f"Playback triggered! ({kind})")
                else:
                    print(f"Error triggering playback: {response.status_code}")
            except Exception as e:
                print(f"Failed to trigger Plexamp: {e}")

        except Exception as e:
            print(f"Reader error: {e}. Reconnecting...")
            pn532 = connect_reader()
