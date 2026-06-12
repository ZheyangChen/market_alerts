# GitHub Actions Deployment

GitHub Actions is the recommended scheduler when the Mac may be asleep or off.

## Required Secret

Create this repository secret:

```text
NTFY_TOPIC
```

Value:

```text
zc-market-alerts-d3b98e07-d0b3-4ce2-b28d-571b8ac97c07
```

Do not commit this value into tracked config files.

For OpenAI-powered summaries, also create:

```text
OPENAI_API_KEY
```

Value: your OpenAI Platform API key.

## Optional Variable

You may create this repository variable:

```text
NTFY_SERVER=https://ntfy.sh
```

If omitted, the workflow defaults to `https://ntfy.sh`.

You may also create:

```text
OPENAI_MODEL=gpt-5.4-mini
```

If omitted, the workflow defaults to `gpt-5.4-mini`.

## Schedules

The first workflow uses UTC schedules that map to EDT:

- 10:00 AM ET snapshot: `14:00 UTC`
- 1:00 PM ET snapshot: `17:00 UTC`
- 4:00 PM ET snapshot: `20:00 UTC`
- emergency checks: `:15`, `:30`, and `:45` during the EDT market window
- 4:20 PM ET market-close digest: `20:20 UTC`

Emergency checks persist alert state in `state/alert_state.json`. The workflow commits that file back to the repository only when a new emergency alert changes it, which keeps later runs from sending the same alert again that day.

This will need review around daylight saving time changes. A later version can add explicit timezone handling if GitHub's current timezone-aware scheduling support is available for the repository.

## Manual Test

Go to GitHub Actions, choose `Market Monitor`, click `Run workflow`, and select:

```text
notify
```

To test OpenAI API access, select:

```text
ai-smoke-test
```

To test the first market-close digest, select:

```text
close-digest
```
