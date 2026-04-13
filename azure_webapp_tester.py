"""
Azure WebApp Tester
====================
Tests de humo y endpoints para cualquier webapp Python en Azure.
Uso: python azure_webapp_tester.py --url https://tu-app.azurewebsites.net
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Optional
import urllib.request
import urllib.error
import urllib.parse


# ──────────────────────────────────────────────
# Colores para la terminal
# ──────────────────────────────────────────────
class Color:
    GREEN  = "\033[92m"
    RED    = "\033[91m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    RESET  = "\033[0m"

def ok(msg):    print(f"  {Color.GREEN}✔{Color.RESET}  {msg}")
def fail(msg):  print(f"  {Color.RED}✘{Color.RESET}  {msg}")
def info(msg):  print(f"  {Color.CYAN}→{Color.RESET}  {msg}")
def warn(msg):  print(f"  {Color.YELLOW}⚠{Color.RESET}  {msg}")
def header(msg):
    print(f"\n{Color.BOLD}{Color.CYAN}{'─'*55}{Color.RESET}")
    print(f"{Color.BOLD}  {msg}{Color.RESET}")
    print(f"{Color.BOLD}{Color.CYAN}{'─'*55}{Color.RESET}")


# ──────────────────────────────────────────────
# Modelo de resultado
# ──────────────────────────────────────────────
@dataclass
class TestResult:
    name: str
    passed: bool
    detail: str = ""
    duration_ms: float = 0.0

@dataclass
class Suite:
    results: list = field(default_factory=list)

    def add(self, r: TestResult):
        self.results.append(r)
        if r.passed:
            ok(f"{r.name}  ({r.duration_ms:.0f} ms)  {r.detail}")
        else:
            fail(f"{r.name}  {Color.RED}{r.detail}{Color.RESET}")

    def summary(self):
        passed = sum(1 for r in self.results if r.passed)
        total  = len(self.results)
        color  = Color.GREEN if passed == total else Color.RED
        header("RESUMEN")
        print(f"  Pasados : {color}{passed}/{total}{Color.RESET}")
        failed = [r for r in self.results if not r.passed]
        if failed:
            print(f"\n  {Color.RED}Tests fallidos:{Color.RESET}")
            for r in failed:
                print(f"    • {r.name}: {r.detail}")
        print()
        return passed == total


# ──────────────────────────────────────────────
# Helper HTTP (sin dependencias externas)
# ──────────────────────────────────────────────
def http_request(
    url: str,
    method: str = "GET",
    headers: dict = None,
    body: Optional[bytes] = None,
    timeout: int = 15,
) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("User-Agent", "AzureWebAppTester/1.0")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except urllib.error.URLError as e:
        raise ConnectionError(str(e.reason)) from e


def timed_request(*args, **kwargs):
    t0 = time.perf_counter()
    result = http_request(*args, **kwargs)
    elapsed = (time.perf_counter() - t0) * 1000
    return result, elapsed


# ──────────────────────────────────────────────
# TESTS DE HUMO
# ──────────────────────────────────────────────
def smoke_tests(base_url: str, suite: Suite, timeout: int):
    header("TESTS DE HUMO")

    # 1. La app responde
    name = "La app responde (HTTP)"
    try:
        (status, headers, _), ms = timed_request(base_url, timeout=timeout)
        passed = status < 500
        suite.add(TestResult(name, passed, f"HTTP {status}", ms))
    except ConnectionError as e:
        suite.add(TestResult(name, False, str(e)))

    # 2. No redirige a error de Azure
    name = "Sin página de error de Azure"
    try:
        (status, _, body), ms = timed_request(base_url, timeout=timeout)
        body_text = body.decode(errors="ignore")
        azure_error = "Application Error" in body_text or "Service Unavailable" in body_text
        suite.add(TestResult(name, not azure_error,
                             "Página de error detectada" if azure_error else f"HTTP {status}", ms))
    except ConnectionError as e:
        suite.add(TestResult(name, False, str(e)))

    # 3. Tiempo de respuesta razonable
    name = "Tiempo de respuesta < 3 s"
    try:
        (_, _, _), ms = timed_request(base_url, timeout=timeout)
        suite.add(TestResult(name, ms < 3000, f"{ms:.0f} ms", ms))
    except ConnectionError as e:
        suite.add(TestResult(name, False, str(e)))

    # 4. HTTPS activo
    name = "HTTPS habilitado"
    if base_url.startswith("https://"):
        suite.add(TestResult(name, True, "URL usa HTTPS"))
    else:
        suite.add(TestResult(name, False, "URL no usa HTTPS"))

    # 5. Cabeceras de seguridad básicas
    name = "Cabeceras de seguridad presentes"
    try:
        (_, headers, _), ms = timed_request(base_url, timeout=timeout)
        headers_lower = {k.lower(): v for k, v in headers.items()}
        missing = []
        for h in ["x-content-type-options", "x-frame-options"]:
            if h not in headers_lower:
                missing.append(h)
        passed = len(missing) == 0
        detail = ("OK" if passed else f"Faltan: {', '.join(missing)}")
        suite.add(TestResult(name, passed, detail, ms))
    except ConnectionError as e:
        suite.add(TestResult(name, False, str(e)))

    # 6. Content-Type presente en la respuesta
    name = "Content-Type en respuesta"
    try:
        (_, headers, _), ms = timed_request(base_url, timeout=timeout)
        headers_lower = {k.lower(): v for k, v in headers.items()}
        ct = headers_lower.get("content-type", "")
        suite.add(TestResult(name, bool(ct), ct or "Ausente", ms))
    except ConnectionError as e:
        suite.add(TestResult(name, False, str(e)))


# ──────────────────────────────────────────────
# TESTS DE ENDPOINTS
# ──────────────────────────────────────────────
@dataclass
class EndpointSpec:
    path: str
    method: str = "GET"
    expected_status: int = 200
    body: Optional[dict] = None
    check_json: bool = False
    description: str = ""

def endpoint_tests(base_url: str, suite: Suite, timeout: int, extra_endpoints: list):
    header("TESTS DE ENDPOINTS")

    # Endpoints comunes a detectar automáticamente
    default_endpoints = [
        EndpointSpec("/",           expected_status=200,  description="Raíz"),
        EndpointSpec("/health",     expected_status=200,  check_json=True, description="Health check"),
        EndpointSpec("/healthz",    expected_status=200,  description="Health check (k8s style)"),
        EndpointSpec("/ping",       expected_status=200,  description="Ping"),
        EndpointSpec("/status",     expected_status=200,  check_json=True, description="Status"),
        EndpointSpec("/api",        expected_status=200,  description="API raíz"),
        EndpointSpec("/api/v1",     expected_status=200,  description="API v1"),
        EndpointSpec("/docs",       expected_status=200,  description="Docs (FastAPI/Swagger)"),
        EndpointSpec("/openapi.json", expected_status=200, check_json=True, description="OpenAPI schema"),
        EndpointSpec("/metrics",    expected_status=200,  description="Métricas"),
        EndpointSpec("/favicon.ico", expected_status=200, description="Favicon"),
        EndpointSpec("/robots.txt", expected_status=200,  description="robots.txt"),
        EndpointSpec("/sitemap.xml", expected_status=200, description="sitemap.xml"),
        EndpointSpec("/404-test-xyz", expected_status=404, description="404 correcto para ruta inexistente"),
    ]

    all_endpoints = default_endpoints + extra_endpoints

    for ep in all_endpoints:
        url  = base_url.rstrip("/") + ep.path
        name = f"{ep.method} {ep.path}" + (f" — {ep.description}" if ep.description else "")
        body_bytes = None
        req_headers = {}
        if ep.body:
            body_bytes = json.dumps(ep.body).encode()
            req_headers["Content-Type"] = "application/json"

        try:
            (status, resp_headers, resp_body), ms = timed_request(
                url, method=ep.method, headers=req_headers,
                body=body_bytes, timeout=timeout
            )
            passed = (status == ep.expected_status)
            detail = f"HTTP {status}"

            if passed and ep.check_json:
                try:
                    json.loads(resp_body)
                    detail += " · JSON válido"
                except json.JSONDecodeError:
                    passed = False
                    detail += " · respuesta no es JSON"

            suite.add(TestResult(name, passed, detail, ms))

        except ConnectionError as e:
            suite.add(TestResult(name, False, str(e)))


# ──────────────────────────────────────────────
# Parser de endpoints extra desde CLI
# ──────────────────────────────────────────────
def parse_extra_endpoints(raw: list[str]) -> list[EndpointSpec]:
    """
    Formato: METHOD:/path:expected_status[:json]
    Ejemplo: POST:/api/login:200:json  o  GET:/about:200
    """
    specs = []
    for item in raw:
        parts = item.split(":")
        if len(parts) < 2:
            warn(f"Endpoint ignorado (formato incorrecto): {item}")
            continue
        method = parts[0].upper()
        path   = parts[1]
        status = int(parts[2]) if len(parts) > 2 else 200
        check_json = len(parts) > 3 and parts[3].lower() == "json"
        specs.append(EndpointSpec(path, method, status, check_json=check_json))
    return specs


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Tester de humo y endpoints para webapp Python en Azure",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python azure_webapp_tester.py --url https://mi-app.azurewebsites.net
  python azure_webapp_tester.py --url https://mi-app.azurewebsites.net --timeout 20
  python azure_webapp_tester.py --url https://mi-app.azurewebsites.net \\
      --endpoints "POST:/api/users:201:json" "GET:/api/health:200"
        """
    )
    parser.add_argument("--url",       required=True, help="URL base de la webapp (ej. https://mi-app.azurewebsites.net)")
    parser.add_argument("--timeout",   type=int, default=15, help="Timeout en segundos por request (default: 15)")
    parser.add_argument("--endpoints", nargs="*", default=[], metavar="SPEC",
                        help="Endpoints extra: METHOD:/path:status[:json]")
    parser.add_argument("--smoke-only", action="store_true", help="Solo ejecutar tests de humo")
    parser.add_argument("--endpoints-only", action="store_true", help="Solo ejecutar tests de endpoints")

    args = parser.parse_args()
    base_url = args.url.rstrip("/")

    print(f"\n{Color.BOLD}🔍 Azure WebApp Tester{Color.RESET}")
    info(f"URL: {base_url}")
    info(f"Timeout: {args.timeout}s")

    suite  = Suite()
    extras = parse_extra_endpoints(args.endpoints)

    if not args.endpoints_only:
        smoke_tests(base_url, suite, args.timeout)

    if not args.smoke_only:
        endpoint_tests(base_url, suite, args.timeout, extras)

    all_passed = suite.summary()
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
