# Live GitHub acceptance — 2026-10-01

Repository: [2063365572abc-bot/qianyan](https://github.com/2063365572abc-bot/qianyan), branch `master`.

Initial published head: `730e89527385faa555bd2ce2607afa25b7136733`. The Git Data API transport verified every uploaded blob, tree and all four local commit hashes before publishing the reference. It preserved local history and used no forced reference update. Native Git HTTPS had ended with an SSL connection timeout; this was a transport fallback.

[GitHub Actions run 36806607997](https://github.com/2063365572abc-bot/qianyan/actions/runs/36806607997) completed successfully for that exact head. Both `backend` and `frontend` jobs passed; the backend job exercised PostgreSQL 16 migrations and the 128-test suite, and the frontend job built the application.

The local authenticated owner API, independent worker and real GitHub adapter then observed this selected repository. Four objective tasks became Done from live evidence: repository exists, README passes the declared structure rule, the `backend/app` directory exists, and the selected CI passes at the observed head SHA. Five GitHub evidence records were recorded with `data_mode=live`. A directory existing does not prove MVP acceptance, and README structure does not prove all installation instructions work.

The owner still has pending tasks for actual model/WeCom integration, public deployment, cross-day acceptance and final demo material. This acceptance used no NVIDIA inference and delivered no WeCom message. It does not complete the full project.

Ignored local evidence: `.local/github-publication.json` and `.local/live-github-evidence.json`. Passwords, tokens, owner session data and private learner materials are not included in this public note.

Official transport references: [Git trees](https://docs.github.com/en/rest/git/trees), [Git commits](https://docs.github.com/en/rest/git/commits), [Git references](https://docs.github.com/en/rest/git/refs). The development publication script is not exposed to the Qianyan agent.
