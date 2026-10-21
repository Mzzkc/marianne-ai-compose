# Glossary

## The three timers first

| Term | What it bounds and what happens when it fires | Scope |
|------|---------------------------------------------|-------|
| **Sheet timeout** (`instrument_config.timeout_seconds`) | The per-sheet execution timeout bounds one attempt; expiry ends that attempt as a timeout, subject to retry and fallback ([reference](configuration-reference.md#top-level-fields)). | One-shot and recurring scores; also configurable per movement, per sheet, and in an instrument alias. |
| **Idle timeout** (`stale_detection.idle_timeout_seconds`) | The opt-in no-output idle timer marks a running sheet stale after the maximum seconds of inactivity, with consecutive idle-and-still-alive checks before the sheet is killed ([reference](configuration-reference.md#stale_detection)). | One-shot and recurring scores; requires `stale_detection.enabled: true`. |
| **Tick window** (`schedule.max_wall_seconds`) | An absolute deadline from the tick's admission bounds one scheduled tick of a recurring score; expiry ends the tick, and pause, resume, pending admission, and restart do not reset it ([reference](configuration-reference.md#recurring-scores-and-wall-limits)). | Scheduled ticks only; a one-shot score has no absolute tick deadline. |

For one-shot scores, per-sheet timeouts, idle detection and the conductor's
accumulated RUNNING budget form the execution envelope; paused time and downtime
do not consume that conductor budget.

## Find the thing you are working with

- **Score** (`mzt run`): the YAML orchestration you submit for execution, called a job internally, whose run id is the `JOB_ID` argument used by monitoring commands ([reference](cli-reference.md#mzt-run)).
- **Sheet** (`sheet`): one execution unit of a score, with its prompt, attempts and acceptance checks ([reference](configuration-reference.md#sheet)).
- **Movement** (`movements`): a named phase that groups an instrument, settings and parallel voices at a numbered stage ([reference](configuration-reference.md#movements)).
- **Voice** (`movements.<k>.voices`): one parallel instance within a movement, with the voice count expanding through the fan-out engine ([reference](configuration-reference.md#movements)).
- **Stage** (concept): the logical stage number shared by a group of parallel sheets, addressed by the stage template variable while instance distinguishes the voices ([reference](score-writing-guide.md#fan-out-variables)).
- **Fan-out** (`sheet.fan_out`): a map from stage numbers to parallel instance counts ([reference](configuration-reference.md#sheet)).
- **Run** (`mzt run`): one execution of a score, submitted immediately by this command even when the score also declares recurrence ([reference](cli-reference.md#mzt-run)).
- **Tick** (`schedule`): a scheduled run of a recurring score, admitted when due and skipped if the previous run is still active ([reference](configuration-reference.md#schedule)).
- **Workspace** (`workspace`): the directory where the score writes its outputs, resolved to an absolute path when loaded ([reference](configuration-reference.md#top-level-fields)).

## Choose who plays

- **Instrument** (`instrument`): the registered profile, score alias or capability class selected to execute a sheet ([reference](configuration-reference.md#top-level-fields)).
- **Capability class** (`mzt instruments classes show`): a portable name such as strong or review that resolves to an ordered chain of instrument profiles ([reference](cli-reference.md#mzt-instruments-classes-showcheckwrite)).
- **Score alias** (`instruments`): a name local to one score that reuses an existing instrument profile with optional settings ([reference](configuration-reference.md#instruments)).
- **Fallback** (`instrument_fallbacks`): the ordered instrument chain used after the primary route cannot continue, with settings available at score, movement and sheet scope ([reference](configuration-reference.md#instrument-fallbacks)).
- **Musician** (concept): the execution agent that plays a sheet and reports its result to the conductor ([reference](marianne-reference.md#the-execution-flow-what-actually-happens)).
- **Technique** (`techniques`): a protocol, MCP server or skill attached to the score and resolved for a phase ([reference](configuration-reference.md#techniques)).

## Supply context and continue work

- **Prelude** (`sheet.prelude`): files or directories injected as context, skills or tools into every sheet ([reference](configuration-reference.md#sheet)).
- **Cadenza** (`sheet.cadenzas`): context injected into specific sheets in addition to the prelude ([reference](configuration-reference.md#sheet)). An agent's `cadenzas/personal/active/` directory is only a folder until a score names it in a `prelude` or `cadenzas` entry.
- **Fermata** (`fermata`): a sheet status that holds execution for a composer decision, handled with the resolve command ([reference](cli-reference.md#mzt-resolve)).
- **Completion mode** (`retry.completion_threshold_percent`): a partial validation pass strictly above this threshold enters targeted completion of the remaining work, while lower results spend normal retries ([reference](configuration-reference.md#retry)).
- **Paused at chain** (`paused_at_chain`): the run status when a successful score's next concert link is held until the composer resumes it ([reference](configuration-reference.md#on_success-post-success-hooks)).

## Tell homonyms apart

- **Conductor, daemon** (`mzt start`): the background process that owns score execution and scheduling, configured separately in conductor.yaml ([reference](cli-reference.md#mzt-start)).
- **Conductor, root key** (`conductor`): deprecated score identity metadata with no runtime reader, which does not configure the daemon ([reference](configuration-reference.md#conductor)).
- **Concert, root key** (`concert`): the configuration for bounded chaining between scores ([reference](configuration-reference.md#concert)).
- **Concert, trigger action** (`sheet.triggers.<k>.on_success[].concert`): an action that submits a child score, also available in failure triggers ([reference](configuration-reference.md#flow-control-expressions-loops-and-triggers)).
- **On success, hook list** (`on_success`): hooks executed after the entire score succeeds ([reference](configuration-reference.md#on_success-post-success-hooks)).
- **On success, trigger list** (`sheet.triggers.<k>.on_success`): actions executed after a sheet attempt succeeds ([reference](configuration-reference.md#flow-control-expressions-loops-and-triggers)).
- **On failure, hook list** (`on_failure`): hooks executed on terminal failure of the entire score ([reference](configuration-reference.md#on_success-post-success-hooks)).
- **On failure, hook policy** (`on_success[].if_hook_fails`): the preferred spelling for continue-or-abort behaviour when a hook itself fails, replacing the deprecated inner on_failure spelling ([reference](configuration-reference.md#on_success-post-success-hooks)).
- **On failure, trigger list** (`sheet.triggers.<k>.on_fail`): the shipped on_fail spelling for failure actions that replace automatic retry, fallback, completion mode and healing for that sheet ([reference](configuration-reference.md#flow-control-expressions-loops-and-triggers)).
- **Validation, acceptance criteria** (`validations`): rules such as file_exists and content_regex checked against a sheet's work ([reference](configuration-reference.md#validations)).
- **Validation, language checker** (`mzt validate`): the command that checks score YAML and reports language findings before submission ([reference](cli-reference.md#mzt-validate)).
- **Validate, score key** (`validate`): the configuration that acknowledges advisory findings through suppressions ([reference](configuration-reference.md#validate)).
- **Approvals** (`approvals`): the preferred spelling for the reserved confirmation section, which has no runtime reader and warns with V327 when set ([reference](configuration-reference.md#checkpoints)).
- **Checkpoints, legacy score key** (`checkpoints`): the deprecated alias for approvals, accepted for one release with V328 and still reserved ([reference](configuration-reference.md#checkpoints)).
- **Checkpoint, saved progress** (concept): persisted execution state used to resume interrupted work, distinct from the reserved approvals section ([reference](cli-reference.md#mzt-resume)).
