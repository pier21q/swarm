"""Server web: niente iframe da altri siti, localhost servito sia su 127.0.0.1 sia su ::1."""
import asyncio
import socket
import unittest

import swarm_web as sw
from swarm_project import Project


def free_port():
    """Una porta libera sia su IPv4 sia su IPv6 locale."""
    for _ in range(50):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        try:
            with socket.socket(socket.AF_INET6) as s6:
                s6.bind(("::1", port))
        except OSError:
            continue
        return port
    raise unittest.SkipTest("nessuna porta libera su entrambi gli indirizzi")


async def get(host, port, path, host_header):
    r, w = await asyncio.open_connection(host, port)
    w.write(f"GET {path} HTTP/1.1\r\nHost: {host_header}\r\n\r\n".encode())
    await w.drain()
    head = (await r.readuntil(b"\r\n\r\n")).decode("latin-1")
    w.close()
    return head


class Web(unittest.TestCase):
    def setUp(self):
        if not socket.has_ipv6:
            self.skipTest("niente IPv6")

    def run_server(self, check):
        async def main():
            dash = sw.WebDashboard(Project(demo=True))
            port = dash.port = free_port()
            server = await sw._listen(dash.handle, port)
            try:
                await check(port)
            finally:
                server.close()
                dash.close_clients()
                await server.wait_closed()
        asyncio.run(main())

    def test_ascolta_su_entrambi_gli_indirizzi(self):
        async def check(port):
            for host, hh in (("127.0.0.1", f"127.0.0.1:{port}"), ("::1", f"[::1]:{port}"),
                             ("::1", f"localhost:{port}")):
                with self.subTest(host=host, header=hh):
                    self.assertIn("200 OK", (await get(host, port, "/", hh)).splitlines()[0])
        self.run_server(check)

    def test_niente_iframe(self):
        async def check(port):
            for path in ("/", "/api/state", "/nessuna", "/events"):
                with self.subTest(path=path):
                    head = await get("127.0.0.1", port, path, f"localhost:{port}")
                    self.assertIn("X-Frame-Options: DENY", head)
                    self.assertIn("frame-ancestors 'none'", head)
            # anche il rifiuto per Host sbagliato
            self.assertIn("X-Frame-Options: DENY", await get("127.0.0.1", port, "/", f"evil.com:{port}"))
        self.run_server(check)

    def test_porta_occupata_su_ipv6(self):
        """Se ::1 è di un altro programma la porta si salta: `localhost` finirebbe da lui."""
        port = free_port()
        other = socket.socket(socket.AF_INET6)
        other.bind(("::1", port))
        other.listen()
        try:
            async def main():
                with self.assertRaises(OSError):
                    await sw._listen(lambda r, w: None, port)
            asyncio.run(main())
        finally:
            other.close()


if __name__ == "__main__":
    unittest.main()
