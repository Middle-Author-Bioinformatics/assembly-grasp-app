# Free MAB accounts

This app uses the shared MAB Cognito pool and profile service. The entry screen has **Create free account** and **Sign in**. Full name, verified email, institution/company and position are required. Favorite bioinformatics software and research area are optional. Any complete MAB account with a result code can view that result.

Configuration is coordinated across all five apps. Follow [the shared deployment guide in gtotree-app](https://github.com/Middle-Author-Bioinformatics/gtotree-app/blob/main/MAB_ACCOUNTS_ALL_APPS.md). Its CloudShell script creates the app-specific OAuth clients, configures the existing service buckets, and sets these main-branch Amplify variables:

- `VITE_MAB_AUTH_ENABLED=true`
- `VITE_MAB_API_BASE=<shared account API>`
- `VITE_MAB_CLIENT_ID=<this app's public client ID>`

The pool/domain default to the existing MAB pool. Do not use GToTree's client ID here; the deployment script creates one for this app, in the same pool. No client secret belongs in frontend configuration.

Merge the coordinated frontend/backend PRs before enabling these variables. With authentication disabled, the legacy app is retained for staged rollout/rollback. After testing, the shared guide's final cutover closes the old anonymous Lambda URLs and makes the input/results buckets private. A frontend login screen alone does not protect old public endpoints.

The account API authorizes result access and excludes raw uploads, logs and diagnostic archives. Scientific assets/downloads can use short-lived signed links that expire after 15 minutes; share the app's result-code URL for durable sharing. HTML reports are fetched with bearer headers and rendered in an isolated sandbox. Account tokens are not placed in report URLs.

Run the account checks with `npm run test:accounts` from the app's package directory (`frontend/`, or the repository root for FeGenie). Existing backend worker formats and job prefixes are preserved.
