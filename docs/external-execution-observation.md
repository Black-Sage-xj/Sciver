# Monitoring externally scheduled containers

The Web collector accepts an operator-owned registry through
`SCIVER_EXECUTION_REGISTRY`. Keep this file and observations outside research
workspaces and outside the public repository. Neither is a scientific verdict.

The registry uses schema `sciver/execution-registry/v1` and maps canonical
`research_root` paths under `tasks`. Each entry binds `task_id`, `task_home`,
`research_root`, `selector`, optional `display_name`, an immutable 64-character
`container_id`, `image_id`, `status_file`, `controller_file`, and the controller's
host `controller_pid` plus `controller_start_ticks`. `observations_file` names a
raw evidence document. The controller file supplies `pending` (ordered selectors)
and `running`; the status file supplies the executor outcome. Registration must
be renewed when a container or controller is replaced.

Run the read-only evidence collector in a separately permissioned service:

```sh
python -m tm_observer.execution_probe --registry /private/registry.json \
  --output /private/observations.json --interval 3
```

The optional `--docker-command` is an operator-supplied JSON argv list. The probe
only uses `inspect`, `top` and `/proc`; the HTTP service needs neither Docker
access nor sudo. Register only operator-verified same-path mounts. Observation
files and their containing directory should be private to the operator.

`external_execution.project` is the single lifecycle interpretation shared by
list, detail, monitor data and CLI-rendered Web panels. Batch exports should call
`collector.collect_run` rather than reconstructing status from Cycle files.
A running container alone does not establish a running research process: the
registered inner PID and start ticks must match an observed, live host process.
Queued tasks retain historical scientific failures separately. Time limits,
scientific attention and official scores remain distinct. Evidence older than
20 seconds is visibly stale. Missing or mismatched identity is unverifiable.
Task controls are refused for registered executors so a stale Web action cannot
launch a second host Runner. Existing ordinary task controls are unchanged.

The list refreshes every five seconds while visible and while no table row has
keyboard focus. Detail views retain their existing polling. Restarting only the
Web or evidence service does not restart containers, queues or model sessions.
