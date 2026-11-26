# Third-party files shipped with Studio

| File | Project | Version | Licence | SHA-256 |
|---|---|---|---|---|
| `static/htmx.min.js` | [htmx](https://htmx.org) | 2.0.4 | BSD Zero Clause (0BSD) | `e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447` |

The file was downloaded from `https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js` and its hash was
checked against the hash that jsDelivr publishes for the same npm package. A test
(`tests/test_ui_targets.py`) keeps the shipped file equal to this hash, so it cannot change
unnoticed. To upgrade, replace the file, update this table and the test.
