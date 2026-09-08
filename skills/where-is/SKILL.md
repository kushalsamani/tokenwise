---
name: where-is
description: Find where a symbol (class, method, function, type, table, enum) is defined without reading files - returns file:line from a local index. Use for "where is X defined", "which file has Y", "find the definition of Z", or before reading a file to locate one symbol.
---
```
{{TOKENWISE}}/tokenwise/whereis.py <Name> [--repo <repo basename>] [--kind class|method|function|type|table|enum] [--limit 15]
{{TOKENWISE}}/tokenwise/whereis.py build          # refresh the index (incremental by mtime; ~1 s for two repos)
```
- Exact-name matches come first, then prefix, then substring. Output is `path:line  kind  name`.
- Then Read ONLY the range you need (offset/limit around that line), not the whole file.
- If the symbol is not found, run `build` once (the file may be new) and retry; only then fall back to Grep.
- Repos indexed are listed in `{{TOKENWISE}}/repos.txt`; add a path there to index another repo.
