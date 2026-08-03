# LONGPLAY RADIO

**A provenance-first radio continuity pipeline built with Genblaze and
Backblaze B2.**

LONGPLAY RADIO turns one continuity line carrying a self-consistent approval
record into a bounded audio artifact, an internally verified Genblaze
provenance manifest, and a durable B2 receipt. It is deliberately small: this
repository contains the DJ media job and nothing else.

Built for the Backblaze Generative Media Hackathon.

Live experience:

- [LONGPLAY RADIO](https://fm.313.cash)
- [Open the live player](https://fm.313.cash/longplay/listen)

The listening experience can link a fan directly to an artist-controlled tip
destination. This worker never holds funds, changes a tip destination, or
inserts payment instructions into a DJ script.

## Why it exists

This demo checks four locally observable conditions: the script matches the
hash in its approval artifact, heuristic screening flags common lyric/contact
patterns before rendering, stored bytes match their read-back hashes, and the
manifest is internally consistent. The standalone job does not authenticate
the approver or prove the semantic absence of lyrics or personal data; a
production caller must do the former and a human reviewer remains responsible
for the latter.

```text
job + approval artifact -> safety contract -> Genblaze Pipeline -> WAV
                                             |          |
                                             v          v
                                      RunBuilder -> private B2
                                             |
                                             v
                                      verified Manifest
```

## Run the no-key demo

Python 3.11 through 3.14 is supported. From the repository root:

```bash
python3 -m venv .venv
. .venv/bin/activate
./scripts/bootstrap.sh dev
cp .env.example .env
set -a
. ./.env
set +a
longplay-radio validate examples/dj_job.json
longplay-radio doctor
longplay-radio run examples/dj_job.json
pytest
```

The fake renderer is not synthetic speech. It creates a deterministic audible
WAV signal so reviewers can exercise the same Genblaze, hashing, manifest, and
storage path without a voice model, network call, or paid key. The command
prints a JSON receipt. Local artifacts are written beneath `var/storage/` and
are ignored by version control.

### Published dependency metadata note

The PyPI wheel for `genblaze-core==0.3.8` still declares `Pillow<12`. The
[official Genblaze PR #243](https://github.com/backblaze-labs/genblaze/pull/243)
fixed that metadata. This project pins the
[reviewed upstream commit](https://github.com/backblaze-labs/genblaze/commit/afa732059c961b8a2c91c5eecaa0c6c2e578f137),
whose Core package allows
`Pillow>=10,<13`, together with security-fixed `Pillow==12.3.0`. The bootstrap
ends with `pip check`; dependency inconsistency is a hard failure. Switch back
to a PyPI pin when the next compatible Core wheel is published.

## The job boundary

`examples/dj_job.json` is synthetic and complete. A valid v1 job contains:

- one canonical printable line, 20–360 characters and 8–55 words;
- station, track title, and artist metadata that must all occur in the line;
- a 5–24 second target duration;
- an asserted timezone-aware approval time and opaque approval reference;
- explicit false attestations for lyrics and personal data;
- SHA-256 of the exact canonical line;
- generic-synthetic-voice rights, never a clone sample or private voice skin.

Unknown keys, contact data, links, lyric labels, repeated five-word phrases,
hash mismatches, and over-fast scripts fail before Genblaze runs. A job cannot
choose a Python class, executable, model, bucket, remote, or credential.

## Real local speech with generic Coqui

Install Coqui TTS separately and choose an operator-controlled single-speaker
model. Then edit `.env`:

```bash
LONGPLAY_RENDERER=coqui
LONGPLAY_COQUI_BIN=/absolute/path/to/tts
LONGPLAY_COQUI_MODEL=tts_models/en/ljspeech/tacotron2-DDC
```

Run the same command:

```bash
longplay-radio run examples/dj_job.json
```

The adapter executes a fixed argument vector without a shell. It has no
speaker-wav, voice-cloning, URL, custom filter, or payload-supplied flag. It
accepts only a bounded readable WAV from the configured generic model.

## Durable Backblaze B2

Use a private, versioned bucket and a least-privilege application key scoped to
that bucket. Direct S3 mode is the strongest receipt:

```bash
LONGPLAY_STORAGE=b2_s3
B2_BUCKET=your-private-versioned-bucket
B2_REGION=us-west-004
B2_KEY_ID=set-in-your-secret-manager
B2_APP_KEY=set-in-your-secret-manager
```

Each object uses a fresh run UUID and object UUID. After `PutObject`, the worker
requires B2's returned `VersionId`, reads that exact `(key, VersionId)`, and
checks returned version, declared size, SHA-256 metadata, downloaded size, and
downloaded SHA-256 before it emits a receipt. It never verifies “latest.” An
opaque store ID also binds every receipt to its configured B2 region and bucket
without publishing either value.

## Genblaze is real here

Both fake and Coqui rendering run as synchronous providers inside the pinned
`genblaze_core.Pipeline` API. After verified storage, the worker builds the
durable run with `RunBuilder` and `StepBuilder`, creates
`Manifest.from_run(...)`, and refuses completion unless `Manifest.verify()`
passes. That checks internal canonical integrity; it is not a signature or an
independent proof of execution authenticity. The worker separately reads back
and hashes every stored object. The manifest is then stored and read back
through the selected backend like every other object.

## How Codex and GPT-5.6 were used

Codex and GPT-5.6 were engineering collaborators for this submission. They
helped reduce a larger prototype to this single public boundary, threat-model
the job and storage contracts, implement adapters, generate adversarial tests,
cross-check that documented commands match the CLI, and review the repository
for private configuration or unrelated product code. Human decisions still
define the product, approve every script, choose the renderer, and control what
ships. Model output never substitutes for the payload's self-consistency and
safety gates enforced at run time.

## More detail

- [Architecture](ARCHITECTURE.md)
- [Security model](SECURITY.md)
- [Evaluation guide](EVALUATION.md)

This is an independent, from-scratch submission rather than a fork or export
of a production application. It contains no production media, credentials,
private voice data, or internal deployment configuration. Source is available
under the evaluation terms in [LICENSE](LICENSE); it is not offered under an
open-source or production-use license.
