"""Untrusted PDF parser entrypoint. Runs only in a disposable subprocess."""
import io
import sys


def main() -> None:
    # Los límites de recursos se aplican en producción Linux. macOS informa
    # RLIM_INFINITY con una forma que impide reducirlo de manera portable; el
    # proceso padre conserva allí el timeout y el límite de entrada.
    if sys.platform.startswith("linux"):
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    from pypdf import PdfReader

    data = sys.stdin.buffer.read(16 * 1024 * 1024 + 1)
    if len(data) > 16 * 1024 * 1024:
        raise ValueError("PDF exceeds limit")
    reader = PdfReader(io.BytesIO(data))
    chunks: list[str] = []
    remaining = 8000
    for index, page in enumerate(reader.pages):
        if index >= 10 or remaining <= 0:
            break
        text = (page.extract_text() or "")[:remaining]
        chunks.append(text)
        remaining -= len(text) + 1
    sys.stdout.buffer.write("\n".join(chunks).encode("utf-8")[:32000])


if __name__ == "__main__":
    main()
