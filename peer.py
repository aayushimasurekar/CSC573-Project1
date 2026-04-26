import socket
import threading
import os
import sys
import datetime
import platform


SERVER_PORT = 7734
VERSION     = "P2P-CI/1.0"
RFC_DIR     = "rfcs"


def get_hostname():
    return socket.gethostname()

def get_os():
    return platform.system() + " " + platform.release()



def recv_until_double_crlf(sock):
    data = b""
    while not data.endswith(b"\r\n\r\n"):
        byte = sock.recv(1)
        if not byte:
            if data:
                break   # connection closed mid-stream, process what we have
            return None # connection closed before any data arrived
        data += byte
    return data.decode()



def recv_p2s_response(sock):
    data = b""
    double_crlf_count = 0
    while double_crlf_count < 2:
        byte = sock.recv(1)
        if not byte:
            break
        data += byte
        if data.endswith(b"\r\n\r\n"):
            double_crlf_count += 1
            text_so_far = data.decode()
            lines = [l for l in text_so_far.strip().split("\r\n") if l]
            if double_crlf_count == 1:
                status_line = lines[0] if lines else ""
                # No-body responses: 400, 404, 505, or 200 with empty data
                # We distinguish by checking if any RFC data line exists
                has_data = any(l.startswith("RFC") for l in lines)
                if not has_data and any(code in status_line for code in ["400", "404", "505"]):
                    break
    return data.decode()


def recv_p2p_response(sock):
    headers_text = recv_until_double_crlf(sock)

    content_length = 0
    for line in headers_text.split("\r\n"):
        if line.lower().startswith("content-length:"):
            content_length = int(line.split(":")[1].strip())
            break

    body = b""
    while len(body) < content_length:
        chunk = sock.recv(4096)
        if not chunk:
            break
        body += chunk

    return headers_text, body



def handle_download_request(conn, addr):
    try:
        request_text = recv_until_double_crlf(conn)
        if not request_text:
            conn.sendall(f"{VERSION} 400 Bad Request\r\n\r\n".encode())
            return
        print(f"\n[UPLOAD] Request from {addr}:\n{request_text.replace(chr(13), '')}")

        lines = request_text.strip().split("\r\n")

        # Validate request line: GET RFC <num> P2P-CI/1.0
        parts = lines[0].split()
        if len(parts) != 4:
            conn.sendall(f"{VERSION} 400 Bad Request\r\n\r\n".encode())
            return

        method, rfc_kw, rfc_num, version = parts

        if version != VERSION:
            conn.sendall(f"{VERSION} 505 P2P-CI Version Not Supported\r\n\r\n".encode())
            return

        if method != "GET" or rfc_kw != "RFC":
            conn.sendall(f"{VERSION} 400 Bad Request\r\n\r\n".encode())
            return

        # Parse headers
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                key, _, val = line.partition(":")
                headers[key.strip()] = val.strip()

        if "Host" not in headers:
            conn.sendall(f"{VERSION} 400 Bad Request\r\n\r\n".encode())
            return

        # Find file
        rfc_file = os.path.join(RFC_DIR, f"rfc{rfc_num}.txt")
        if not os.path.exists(rfc_file):
            conn.sendall(f"{VERSION} 404 Not Found\r\n\r\n".encode())
            return

        # Send file
        with open(rfc_file, "rb") as f:
            file_data = f.read()

        now      = datetime.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
        modified = datetime.datetime.utcfromtimestamp(
                       os.path.getmtime(rfc_file)
                   ).strftime("%a, %d %b %Y %H:%M:%S GMT")

        response = (
            f"{VERSION} 200 OK\r\n"
            f"Date: {now}\r\n"
            f"OS: {get_os()}\r\n"
            f"Last-Modified: {modified}\r\n"
            f"Content-Length: {len(file_data)}\r\n"
            f"Content-Type: text/plain\r\n"
            f"\r\n"
        )
        conn.sendall(response.encode() + file_data)
        print(f"[UPLOAD] Sent RFC {rfc_num} to {addr} ({len(file_data)} bytes)")

    except Exception as e:
        print(f"[UPLOAD] Error: {e}")
        try:
            conn.sendall(f"{VERSION} 400 Bad Request\r\n\r\n".encode())
        except:
            pass
    finally:
        conn.close()


def start_upload_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', 0))
    port = sock.getsockname()[1]
    sock.listen(10)
    print(f"[UPLOAD] Upload server listening on port {port}")

    def serve():
        while True:
            try:
                conn, addr = sock.accept()
                t = threading.Thread(target=handle_download_request,
                                     args=(conn, addr), daemon=True)
                t.start()
            except Exception as e:
                print(f"[UPLOAD] Error: {e}")
                break

    threading.Thread(target=serve, daemon=True).start()
    return port



def send_add(server_conn, rfc_num, title, hostname, upload_port):
    request = (
        f"ADD RFC {rfc_num} {VERSION}\r\n"
        f"Host: {hostname}\r\n"
        f"Port: {upload_port}\r\n"
        f"Title: {title}\r\n"
        f"\r\n"
    )
    print(f"\n[Peer] Sending:\n{request.replace(chr(13), '')}", end="")
    server_conn.sendall(request.encode())
    response = recv_p2s_response(server_conn)
    print(f"[Peer] Response:\n{response.replace(chr(13), '')}")


def send_lookup(server_conn, rfc_num, title, hostname, upload_port):
    request = (
        f"LOOKUP RFC {rfc_num} {VERSION}\r\n"
        f"Host: {hostname}\r\n"
        f"Port: {upload_port}\r\n"
        f"Title: {title}\r\n"
        f"\r\n"
    )
    print(f"\n[Peer] Sending:\n{request.replace(chr(13), '')}", end="")
    server_conn.sendall(request.encode())
    response = recv_p2s_response(server_conn)
    print(f"[Peer] Response:\n{response.replace(chr(13), '')}")

    # FIX: parse peers robustly — hostname is second-to-last token, port is last
    # This handles RFC titles with spaces correctly.
    peers = []
    found_title = ""
    for line in response.strip().split("\r\n"):
        line = line.strip()
        if line.startswith("RFC"):
            tokens = line.split()
            # Format: RFC <num> <title words...> <hostname> <port>
            # hostname and port are always the last two tokens
            if len(tokens) >= 4:
                peer_host = tokens[-2]
                peer_port = int(tokens[-1])
                peers.append((peer_host, peer_port))
                if not found_title:
                    # title = everything between RFC <num> and <hostname>
                    found_title = " ".join(tokens[2:-2])
    return peers, found_title


def send_list(server_conn, hostname, upload_port):
    request = (
        f"LIST ALL {VERSION}\r\n"
        f"Host: {hostname}\r\n"
        f"Port: {upload_port}\r\n"
        f"\r\n"
    )
    print(f"\n[Peer] Sending:\n{request.replace(chr(13), '')}", end="")
    server_conn.sendall(request.encode())
    response = recv_p2s_response(server_conn)
    print(f"[Peer] Response:\n{response.replace(chr(13), '')}")



def download_rfc(rfc_num, peer_host, peer_port, hostname):
    try:
        print(f"\n[DOWNLOAD] Connecting to {peer_host}:{peer_port} for RFC {rfc_num}...")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((peer_host, peer_port))

        request = (
            f"GET RFC {rfc_num} {VERSION}\r\n"
            f"Host: {peer_host}\r\n"
            f"OS: {get_os()}\r\n"
            f"\r\n"
        )
        print(f"\n[Peer] Sending:\n{request.replace(chr(13), '')}", end="")
        sock.sendall(request.encode())

        headers_text, body = recv_p2p_response(sock)
        sock.close()

        print(f"[Peer] Response:\n{headers_text.replace(chr(13), '')}")

        if "200 OK" not in headers_text.split("\r\n")[0]:
            print(f"[DOWNLOAD] Failed.")
            return False

        os.makedirs(RFC_DIR, exist_ok=True)
        rfc_file = os.path.join(RFC_DIR, f"rfc{rfc_num}.txt")
        with open(rfc_file, "wb") as f:
            f.write(body)

        print(f"[DOWNLOAD] Saved to {rfc_file} ({len(body)} bytes)")
        return True

    except Exception as e:
        print(f"[DOWNLOAD] Error: {e}")
        return False



def get_local_rfcs():
    os.makedirs(RFC_DIR, exist_ok=True)
    rfcs = []
    for fname in os.listdir(RFC_DIR):
        if fname.startswith("rfc") and fname.endswith(".txt"):
            rfc_num = fname[3:-4]
            fpath   = os.path.join(RFC_DIR, fname)
            try:
                with open(fpath, "r") as f:
                    title = f.readline().strip() or "Unknown RFC"
            except:
                title = "Unknown RFC"
            rfcs.append((rfc_num, title))
    return rfcs



def main():
    # FIX: server host is no longer hardcoded — pass as argument
    server_host = sys.argv[1] if len(sys.argv) > 1 else "localhost"

    hostname    = get_hostname()
    upload_port = start_upload_server()

    print(f"[Peer] Hostname: {hostname}")
    print(f"[Peer] Connecting to server {server_host}:{SERVER_PORT}...")

    try:
        server_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_conn.connect((server_host, SERVER_PORT))
        print(f"[Peer] Connected to server at port {SERVER_PORT}")
    except Exception as e:
        print(f"[Peer] Cannot connect to server: {e}")
        sys.exit(1)

    # Register local RFCs
    local_rfcs = get_local_rfcs()
    if local_rfcs:
        for rfc_num, title in local_rfcs:
            print(f"[Peer] Registering RFC {rfc_num}: {title}")
            send_add(server_conn, rfc_num, title, hostname, upload_port)
    else:
        print(f"[Peer] No RFCs found in '{RFC_DIR}/'")

    # Menu
    while True:
        print("\n" + "="*40)
        print("1. ADD    — register an RFC")
        print("2. LOOKUP — find peers with an RFC")
        print("3. LIST   — list all RFCs on server")
        print("4. GET    — download an RFC from a peer")
        print("5. EXIT")
        print("="*40)
        choice = input("Choice: ").strip()

        if choice == "1":
            rfc_num = input("RFC number: ").strip()
            title   = input("RFC title: ").strip()
            send_add(server_conn, rfc_num, title, hostname, upload_port)

        elif choice == "2":
            rfc_num = input("RFC number: ").strip()
            title   = input("RFC title (press Enter to skip): ").strip()
            send_lookup(server_conn, rfc_num, title, hostname, upload_port)

        elif choice == "3":
            send_list(server_conn, hostname, upload_port)

        elif choice == "4":
            rfc_num = input("RFC number: ").strip()
            title   = input("RFC title (press Enter to skip): ").strip()

            peers, found_title = send_lookup(server_conn, rfc_num, title, hostname, upload_port)
            # Use title from LOOKUP response if user didn't provide one
            if not title and found_title:
                title = found_title

            if not peers:
                print("[PEER] No peers have that RFC.")
                continue

            rfc_file = os.path.join(RFC_DIR, f"rfc{rfc_num}.txt")
            if os.path.exists(rfc_file):
                print(f"[PEER] You already have RFC {rfc_num} locally.")
                overwrite = input("Download again anyway? (y/n): ").strip().lower()
                if overwrite != "y":
                    continue

            for peer_host, peer_port in peers:
                if peer_host == hostname and peer_port == upload_port:
                    continue
                if download_rfc(rfc_num, peer_host, peer_port, hostname):
                    send_add(server_conn, rfc_num, title, hostname, upload_port)
                    break
            else:
                print(f"[PEER] Could not download RFC {rfc_num} from any peer.")

        elif choice == "5":
            print("[PEER] Disconnecting...")
            server_conn.close()
            sys.exit(0)

        else:
            print("Invalid choice.")


if __name__ == "__main__":
    main()