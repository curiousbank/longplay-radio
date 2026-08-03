# Evaluation guide

## Reviewer path

Use the no-key commands in the README. A successful run should produce:

- a `validated` response before any generation;
- a doctor report naming `fake` and `local`, with no credential values;
- one audible WAV signal and one approved-script JSON artifact;
- immediate storage read-back for both assets;
- an internally verified Genblaze manifest stored through the same backend;
- a final JSON receipt with exact SHA-256, size, storage key, and version token
  for each object.

## Pass criteria

| Property | Evidence |
|---|---|
| Runs without paid services | Fake renderer + local backend |
| Real Genblaze orchestration | Pipeline provider step and resulting run ID |
| Internally verified provenance | RunBuilder, Manifest.from_run, Manifest.verify |
| Script self-consistency | Approval-artifact SHA equals canonical script SHA |
| Heuristic lyric/privacy boundary | Adversarial contract tests |
| Durable-object integrity | Upload/read-back size and SHA checks |
| B2 version binding | Direct adapter requires exact VersionId on PUT and GET |
| No private voice material | Generic Coqui adapter has no cloning input |
| Reproducible review | Synthetic committed job and deterministic fake WAV |

## Useful test selections

After the README setup:

```bash
pytest tests/test_contract.py
pytest tests/test_pipeline.py
pytest tests/test_storage.py
pytest tests/test_coqui.py
```

The B2 unit test uses an in-memory S3 client and exercises exact-version request
behavior without network access. Real B2 validation should use a disposable
private bucket and then inspect the returned version IDs.

## Evaluation license

The repository is public for transparent review, testing, and judging under
the source-available terms in `LICENSE`. Those terms do not grant production,
commercial, hosted-service, or redistribution rights.
