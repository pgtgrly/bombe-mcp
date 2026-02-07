"""Repository structure map generation backend."""

from __future__ import annotations

from collections import defaultdict
from contextlib import closing

from bombe.models import StructureRequest
from bombe.store.database import Database


def _approx_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, int(len(text) / 3.5))


def get_structure(db: Database, req: StructureRequest) -> str:
    path_like = req.path if req.path not in {"", "."} else "%"
    if path_like != "%" and not path_like.endswith("%"):
        path_like = f"{path_like.rstrip('/')}/%"

    with closing(db.connect()) as conn:
        rows = conn.execute(
            """
            SELECT file_path, name, kind, signature, pagerank_score
            FROM symbols
            WHERE file_path LIKE ?
            ORDER BY pagerank_score DESC, file_path ASC, start_line ASC;
            """,
            (path_like,),
        ).fetchall()

    grouped: dict[str, list[tuple[str, str, str, float]]] = defaultdict(list)
    for row in rows:
        grouped[row["file_path"]].append(
            (
                row["name"],
                row["kind"],
                row["signature"] or "",
                float(row["pagerank_score"] or 0.0),
            )
        )

    lines: list[str] = []
    rank = 0
    for file_path in sorted(grouped.keys()):
        lines.append(file_path)
        for name, kind, signature, _score in grouped[file_path]:
            rank += 1
            marker = "[TOP] " if rank <= 10 else ""
            detail = signature if req.include_signatures and signature else f"{kind} {name}"
            lines.append(f"  {marker}{detail}  [rank:{rank}]")

    output_lines: list[str] = []
    used_tokens = 0
    for line in lines:
        line_tokens = _approx_tokens(line)
        if used_tokens + line_tokens > req.token_budget:
            break
        output_lines.append(line)
        used_tokens += line_tokens

    return "\n".join(output_lines)
