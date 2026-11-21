# RWX Blast Radius

A regression test selector for SQLGlot at `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`. The entry reads the grader's test catalogue and unified diffs, then writes one JSON line per patch. At scoring time it uses the Python standard library and two checked-in indexes generated from the original revision.

## Results

| Evaluation | Changes | Simulated seconds | Missed changes |
|---|---:|---:|---:|
| Organizer's starter on the supplied sample | 30 | 1,210.470 | 0 |
| This entry on the same sample | 30 | 113.490 | 0 |
| Three independent validation changes | 3 | 6.887 | 0 |

The sample improvement is 90.62%. The organizer's unmodified scorer calculated both sample scores. The independent changes altered MySQL SQL generation, edited a fixture and a test together, and broke a test-module import. The full pinned suite was run on each altered checkout to identify the failing tests and the unimportable file. A further 60-patch check permuted the test numbers, renamed every patch and introduced a previously unseen test. The remapped selections matched in all 60 cases.

The production Dockerfile built for Linux AMD64. Three runs of the exported image filesystem and its default command produced byte-identical output under UID 65534, two CPUs, a read-only root and a separate network namespace. Median local CPU time including the test harness was 1.15 seconds for the entire sample batch. The official score adds container CPU time and uses hidden changes. A public-sample score measures behavior on those 30 changes.

## Build and score the sample

Use Docker with Linux containers, Make and Python 3.9 or newer for the organizer's local scorer. On Windows, these commands can run in WSL with Docker Desktop integration. Start in the folder containing this README and the Dockerfile. Put the organizer's starter archive in the parent folder or adjust its path.

```sh
mkdir -p kit
tar -xzf ../blast-radius-sample.tar.gz -C kit
make sample
```

If the starter is already unpacked elsewhere, use `make sample KIT=/path/to/starter`. The Makefile mounts only its `sample/commits` directory and `sample/tests.json` into the container. The separate host-side scorer reads `answers.json` after the container exits. The `kit` folder and generated `output.jsonl` are excluded from Git.

Run the focused regression tests with `make test` using Python 3.10 or later. The scored container uses the organizer's pinned Python 3.13 image.

## Selection and scheduling

1. **Align each patch.** Validate hunk lengths and context against the pinned source. Deleted lines use original coordinates. Insertions also include their surrounding scope. Each patch starts from the same revision.
2. **Find affected tests.** For a function edit, look up tests that executed the changed lines and the enclosing statement or condition. For class configuration, combine observed class and subclass use with source references. For a fixture, include the tests that opened the file. Shared setup observations apply to the tests in their scope. Cold imports and serialization across process-pool boundaries are recorded explicitly.
3. **Check comparisons where evidence permits.** If the source trees differ in a single binary comparison, evaluate both expressions over the supported operand values observed at that location. A test can be removed when every represented pair gives the same result. Unknown operators, custom comparisons, incomplete observations and truncated value sets keep the test. Explicit source readers and cold-import dependencies remain eligible.
4. **Handle uncertainty.** Unsupported patches and unfamiliar file changes use a broader selection. New test IDs are included. The runtime resolves pytest IDs against the grader's current test numbers.
5. **Price worker assignments.** Durations are converted to integer milliseconds. For one through eight workers, place longer tests on the least-loaded worker and compare the largest load plus 2,000 ms per occupied worker. A lower bound skips worker counts that cannot improve the current plan. The selector uses the cheapest assignment it constructs.

Coverage is measured on one revision and execution environment. Different process interleavings, reflection, shared state, or code paths absent from a baseline run can affect the accuracy of a selection. Opaque file changes may select the whole suite.

## Index provenance and regeneration

`coverage.json` records test IDs, line coverage, cold imports, explicit file reads, and class and symbol use. `operands.json` records bounded baseline operand observations at individual comparison sites. Both use shared test bitsets. Neither index uses a patch ID, sample patch, failure label or precomputed response to an edit. Selection happens after the incoming diff is read.

The scripts under `tools/` reproduce the indexes. Use a clean SQLGlot checkout at the revision above and the organizer's Python 3.13 environment with `tools/requirements.lock` installed. Run the following from that checkout with `ENTRY` set to the absolute path of this repository.

```sh
export ENTRY=/absolute/path/to/rwx-blast-radius
export PYTHONPATH="$ENTRY/tools:$PWD"
export SKIP_INTEGRATION=0 OPENBLAS_NUM_THREADS=1 PYTHON_CPU_COUNT=2
export PYTHONHASHSEED=0 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
TRACE_ROOT=$(mktemp -d)
export RWX_TRACE_DIR="$TRACE_ROOT/lines"
python "$ENTRY/tools/trace_tests.py"
python "$ENTRY/tools/trace_imports.py" "$RWX_TRACE_DIR"
python "$ENTRY/tools/build_index.py" "$RWX_TRACE_DIR" "$ENTRY/coverage.json" --source "$PWD"
export RWX_TRACE_DIR="$TRACE_ROOT/values"
RWX_PROFILE_VALUES=1 python "$ENTRY/tools/trace_tests.py"
python "$ENTRY/tools/build_values.py" "$RWX_TRACE_DIR" "$ENTRY/operands.json"
```

The baseline coverage run observed 1,620 passing tests, one skipped test and 25,440 passing subtests. The file-read and process-pool observations come from executing the upstream tests. The independent operand run also passed the same baseline suite. The image build uses the checked-in results.

## Publish the entry

The distributed ZIP includes the tested Git commit. Extract it, open the project folder and run `git rev-parse HEAD`. The hash printed is the commit to enter on the competition form.

With the GitHub CLI signed in to your team account, run the following from the extracted project folder. Choose an available repository name on your account.

```sh
gh repo create rwx-blast-radius --public --source=. --remote=origin --push
git rev-parse HEAD
```

GitHub Desktop also supports this workflow. Choose **File → Add local repository**, select the extracted folder, choose **Publish repository**, clear **Keep this code private**, and publish. Paste the public repository URL and the full commit hash into the RWX track submission form.

## Credits and license

The Docker base digest, dependency lock, test catalogue format, evaluation procedure and worker limits are from the Builders Buildathon RWX starter kit. The organizer's scorer is used separately for validation. SQLGlot is MIT-licensed by Toby Mao and contributors. Its original license is in `licenses/SQLGlot-LICENSE`. The Docker build retrieves the exact upstream revision, verifies a content digest over the Python files and license, and retains the upstream license in the image. This entry was developed with AI assistance and is released under the accompanying MIT license.

[RWX track and official rules](https://www.joinbuilders.org/buildathon/tracks/blast-radius) · [Pinned SQLGlot source](https://github.com/tobymao/sqlglot/tree/eb11aec4be8cb2122afe713a4816662c3f0fb2fa)
