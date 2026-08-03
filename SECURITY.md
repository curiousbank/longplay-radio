# Security model

## Trust boundaries

Untrusted input is the complete JSON job, including its approval reference,
attestations, and script digest. Trusted configuration is the process
environment and operator-installed executables. Backblaze B2 is a remote
system whose writes must be independently read back.

The demonstration verifies only that the approval artifact is internally
self-consistent. It does not authenticate an approver. A production caller
must authenticate and authorize the opaque approval reference before creating
a job.

## Enforced controls

- Strict schemas reject unknown fields and non-canonical JSON values.
- Scripts are one printable NFC line with character, word, and speaking-rate
  bounds.
- Metadata must appear in the approved line, and heuristic screening rejects
  common phone/email/address/card-shaped data, URLs, lyric labels, and repeated
  five-word phrases.
- A SHA-256 mismatch or non-false privacy/lyrics attestation fails before a
  provider is constructed.
- Jobs cannot select providers, models, executables, buckets, remotes, paths,
  credentials, speaker samples, or command flags.
- Coqui runs without a shell, with a small environment allowlist, a timeout,
  and a private temporary output directory.
- Generated audio is local, readable WAV, no longer than 30 seconds and no
  larger than 16 MiB before upload.
- Object keys are generated from UUIDs and digests inside a fixed prefix.
- Direct B2 writes require exact version IDs and byte-for-byte read-back.
- Receipts contain no secret, presigned URL, customer identifier, or local
  filesystem path.

## Residual risks

- Pattern checks cannot prove the semantic absence of lyrics, personal data,
  or copyright risk. The job's approval and rights fields are assertions until
  an external authority authenticates them.
- A generic TTS model can have upstream model or dependency risks. Pin the
  model artifact independently and run the adapter under OS-level resource and
  network restrictions in production.
- A compromised host can read process credentials and generated plaintext.
  Use short-lived workers, private buckets, least-privilege keys, encrypted
  disks, and a separate deletion role.
- This repository does not implement caller authentication or the upstream
  approval database.

## Secret handling

Never commit `.env`, B2 keys, generated WAVs, or real job payloads. Use a secret
manager for service deployments. Scope B2 keys to a
single private bucket with only the capabilities the selected adapter needs.
Do not grant this worker bucket deletion permission.

Report a vulnerability through the repository's private security-advisory
feature. Do not open a public issue containing credentials or exploit data.
