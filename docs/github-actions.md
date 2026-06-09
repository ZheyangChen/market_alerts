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

## Optional Variable

You may create this repository variable:

```text
NTFY_SERVER=https://ntfy.sh
```

If omitted, the workflow defaults to `https://ntfy.sh`.

## Schedules

The first workflow uses UTC schedules that map to EDT:

- 10:00 AM ET snapshot: `14:00 UTC`
- 1:00 PM ET snapshot: `17:00 UTC`
- 4:00 PM ET snapshot: `20:00 UTC`
- 4:20 PM ET market-close placeholder: `20:20 UTC`
- emergency checks: every 15 minutes during the EDT market window

This will need review around daylight saving time changes. A later version can add explicit timezone handling if GitHub's current timezone-aware scheduling support is available for the repository.

## Manual Test

Go to GitHub Actions, choose `Market Monitor`, click `Run workflow`, and select:

```text
notify
```
