import time
import os
import glob
import serial
from adafruit_pn532.uart import PN532_UART

from plexamp import InvalidPlaybackURL, PlexampClient, prepare_playback_url

# ----------------------------
# Helper: find PN532 serial device
# ----------------------------
def find_uart_device():
    devices = glob.glob("/dev/ttyUSB*")
    return devices[0] if devices else None

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
        device = find_uart_device()
        if not device:
            print("Waiting for NFC reader...")
            time.sleep(2)
            continue
        try:
            ser = serial.Serial(device, baudrate=115200, timeout=1)
            pn532 = PN532_UART(ser, debug=False)
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
    plexamp = PlexampClient()

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
            try:
                local_url = prepare_playback_url(local_url)
            except InvalidPlaybackURL as e:
                print(f"Invalid playback URL: {e}")
                continue
            print(f"Local Plexamp URL: {local_url}")

            # If same URL as before and still within active session, skip
            if local_url == last_url and (now - last_seen_time) <= forget_after:
                print("Same tag & URL already active — skipping trigger.")
                continue

            # Trigger Plexamp playback
            try:
                result = plexamp.play(local_url)
                if result.success:
                    last_url = local_url
                    print(f"Playback triggered! ({kind})")
                else:
                    print(f"Failed to trigger Plexamp: {result.message}")
            except Exception as e:
                print(f"Failed to trigger Plexamp: {e}")

        except Exception as e:
            print(f"Reader error: {e}. Reconnecting...")
            pn532 = connect_reader()

