# Command line

| Command | Purpose |
|---|---|
| `veriledger run SCENARIO.yaml --out DIR [--replications N]` | run a scenario |
| `veriledger demo --out DIR [--replications N]` | run the four bundled scenarios |
| `veriledger simulate [--config FILE] [--seed S] --out DIR [--no-truth]` | write one economy and its ledger |
| `veriledger simulate --scenario FILE --replication R [--sweep-value V] --out DIR` | write the economy of one replication of a scenario |
| `veriledger check FILE [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--periods 4] [--out flags.csv] [--ledger L.jsonl]` | record a SAF-T or general-ledger CSV file on a ledger and list cut-off, duplicate and forward-dated entries; the fiscal year comes from `--start`/`--end`, the SAF-T header, or twelve months from the first transaction; exit code 2 if a file cannot be read or the periods do not fit |
| `veriledger verify LEDGER.jsonl [--commits FILE]` | integrity check against commitments; exit code 1 if it fails, 2 if a file cannot be read |
| `veriledger saft-import FILE.xml --out entries.csv [--start YYYY-MM-DD]` | SAF-T to CSV |
| `veriledger saft-export DIR --out file.xml` | economy bundle (the firm's database as it stands) to a minimal SAF-T 1.40 file of its fiscal year |
| `veriledger psi-bench [--n N] [--backend all]` | time the PSI backends |

`verify` without `--commits` compares the ledger only with the commitments stored next to it, which
whoever controls the file can rewrite, and says so. With `--commits`, it reports that an external file
was supplied but does not assert that its custody or provenance is independent. A complete check of
late status requires the commitment for every period containing records.
