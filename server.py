import socket
import threading
import sys


peer_list = []   
rfc_index = []   
lock = threading.Lock()

VERSION         = "P2P-CI/1.0"
WELL_KNOWN_PORT = 7734



def recv_request(conn):
    data = b""
    while not data.endswith(b"\r\n\r\n"):
        byte = conn.recv(1)
        if not byte:
            return None   # connection closed
        data += byte
    return data.decode()



def parse_request(text):
    lines = text.strip().split("\r\n")

    # Request line
    parts   = lines[0].split()
    method  = parts[0]               # ADD / LOOKUP / LIST
    version = parts[-1]              # P2P-CI/1.0
    rfc_num = parts[2] if len(parts) == 4 else None

    # Headers
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            key, _, val = line.partition(":")
            headers[key.strip()] = val.strip()

    return method, rfc_num, version, headers



def handle_peer(conn, addr):
    print(f"[Server] New TCP connection from {addr[0]}:{addr[1]}")
    peer_hostname = None
    peer_port     = None

    try:
        while True:
            text = recv_request(conn)
            if text is None:
                break

            print(f"[SERVER] Request from {addr}:\n{text.replace(chr(13), '')}")

            # ── Parse ──
            try:
                method, rfc_num, version, headers = parse_request(text)
            except Exception as e:
                print(f"[SERVER] Parse error: {e}")
                conn.sendall(f"{VERSION} 400 Bad Request\r\n\r\n".encode())
                continue

            # ── Version check ──
            if version != VERSION:
                conn.sendall(f"{VERSION} 505 P2P-CI Version Not Supported\r\n\r\n".encode())
                continue

            # ── Validate Host/Port headers are present ──
            req_host = headers.get("Host", "").strip()
            req_port = headers.get("Port", "").strip()

            if not req_host or not req_port:
                conn.sendall(f"{VERSION} 400 Bad Request\r\n\r\n".encode())
                continue

            # ── Register peer on first valid message ──
            if peer_hostname is None:
                peer_hostname = req_host
                peer_port     = req_port
                with lock:
                    peer_list.insert(0, {"hostname": peer_hostname, "port": peer_port})
                print(f"[Server] Connection from host {peer_hostname} at {addr[0]}:{peer_port}")
                print(f"[Server] Added {peer_hostname}:{peer_port}")

            # ── ADD ──
            if method == "ADD":
                if rfc_num is None:
                    conn.sendall(f"{VERSION} 400 Bad Request\r\n\r\n".encode())
                    continue

                title    = headers.get("Title", "")
                hostname = req_host
                port     = req_port

                with lock:
                    already = any(
                        r["rfc_number"] == rfc_num
                        and r["hostname"] == hostname
                        and r["port"] == port
                        for r in rfc_index
                    )
                    if not already:
                        rfc_index.insert(0, {
                            "rfc_number": rfc_num,
                            "title":      title,
                            "hostname":   hostname,
                            "port":       port
                        })
                        print(f"[Server] Added RFC {rfc_num} from {hostname}")

                # FIX: blank line between status line and data body
                response = (
                    f"{VERSION} 200 OK\r\n"
                    f"\r\n"
                    f"RFC {rfc_num} {title} {hostname} {port}\r\n"
                    f"\r\n"
                )

            # ── LOOKUP ──
            elif method == "LOOKUP":
                if rfc_num is None:
                    conn.sendall(f"{VERSION} 400 Bad Request\r\n\r\n".encode())
                    continue

                with lock:
                    matches = [r for r in rfc_index if r["rfc_number"] == rfc_num]

                if not matches:
                    response = f"{VERSION} 404 Not Found\r\n\r\n"
                else:
                    # FIX: blank line between status line and data body
                    response = f"{VERSION} 200 OK\r\n\r\n"
                    for r in matches:
                        response += f"RFC {r['rfc_number']} {r['title']} {r['hostname']} {r['port']}\r\n"
                    response += "\r\n"

            # ── LIST ──
            elif method == "LIST":
                with lock:
                    all_rfcs = list(rfc_index)

                if not all_rfcs:
                    # Return 200 OK with empty body (LIST always succeeds)
                    response = f"{VERSION} 200 OK\r\n\r\n"
                else:
                    # FIX: blank line between status line and data body
                    response = f"{VERSION} 200 OK\r\n\r\n"
                    for r in all_rfcs:
                        response += f"RFC {r['rfc_number']} {r['title']} {r['hostname']} {r['port']}\r\n"
                    response += "\r\n"

            else:
                response = f"{VERSION} 400 Bad Request\r\n\r\n"

            conn.sendall(response.encode())
            print(f"[SERVER] Response to {addr}:\n{response.replace(chr(13), '')}")

    except Exception as e:
        print(f"[SERVER] Error with {addr}: {e}")

    finally:
        if peer_hostname and peer_port:
            with lock:
                before_p = len(peer_list)
                before_r = len(rfc_index)
                peer_list[:] = [p for p in peer_list
                                if not (p["hostname"] == peer_hostname
                                        and p["port"] == peer_port)]
                rfc_index[:] = [r for r in rfc_index
                                if not (r["hostname"] == peer_hostname
                                        and r["port"] == peer_port)]
                print(f"[SERVER] Peer {peer_hostname}:{peer_port} left. "
                      f"Removed {before_p - len(peer_list)} peer(s), "
                      f"{before_r - len(rfc_index)} RFC(s).")
        conn.close()



def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else WELL_KNOWN_PORT

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('', port))
    srv.listen(10)
    print(f"[SERVER] Listening on port {port}...")

    try:
        while True:
            conn, addr = srv.accept()
            t = threading.Thread(target=handle_peer, args=(conn, addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("\n[SERVER] Shutting down.")
    finally:
        srv.close()


if __name__ == "__main__":
    main()