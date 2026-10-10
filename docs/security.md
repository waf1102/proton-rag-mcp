# Security and data paths

The IMAP adapter uses read-only folder selection and `BODY.PEEK[]`. It has no message copy, move, flag-change, delete, or expunge operations. All selectable folders are included unless configured otherwise.

Bridge transport validates the certificate chain and hostname. Trust the certificate obtained from your controlled Bridge installation; do not disable verification. IMAP and application credentials stay in private runtime configuration and out of logs.

MIME bodies and supported attachments are parsed in memory in a disposable worker. Images, executables and archives are not executed. Workers have CPU, memory, time, MIME-part, and document-expansion bounds. Raw private messages are not staged in files by the application.

SQLite persists cached text, metadata and commit manifests. Qdrant persists chunk text, dense vectors and a lexical index. This is not memory-only storage. Protect its volume, the catalog, swap and backups according to your threat model. Rootless containers and local paths do not themselves provide encryption at rest. The application does not configure host disk encryption.

Search and embeddings stay local. `answer_mail` sends the user's question and selected excerpts to OpenRouter and its model provider. It requests providers that deny data collection, but that routing setting is not an independent retention guarantee. Returned mail content is untrusted data; generation has no action tools. MCP clients may apply their own model and data policies to retrieved content.

Mailbox folder names are encoded in citations alongside UIDVALIDITY, UID and a content digest. Citations identify source messages; they do not grant mailbox access. Search results also expose sender, recipients, subject, date and attachment names to the connected client.

Application services bind to host loopback. MCP uses stdio and has no unauthenticated HTTP listener. Logical index deletion removes matching document/vector data; it does not promise forensic erasure from snapshots, backups or old storage pages.

Full extracted message text is cached in the local SQLite catalog for `read_mail`. Protect it like the Qdrant volume. Search, reading, and coverage reporting do not make cloud requests or modify Bridge messages. The untrusted label means mail text is data to analyze, not instructions to execute.
