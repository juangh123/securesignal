# Create the deployment identity

Use the AWS root user only to create and secure the deployment identity. Do not
create an access key on the root user.

## Option A: IAM Identity Center (preferred)

1. In the AWS console, open **IAM Identity Center**.
2. Create a user for deployment and assign it to a permission set containing the
   policy in `deployment-policy.json`.
3. Configure the AWS CLI:

   ```powershell
   aws configure sso
   aws sso login --profile securesignal-deploy
   $env:AWS_PROFILE = "securesignal-deploy"
   aws sts get-caller-identity
   ```

## Option B: Dedicated IAM user

1. Open **IAM > Users > Create user** while signed in as the root user.
2. Name it `securesignal-deploy` and do not enable console access.
3. Create an inline policy from `deployment-policy.json`.
4. Enable MFA for the user.
5. Create one CLI access key for that IAM user.
6. Configure the profile locally:

   ```powershell
   aws configure --profile securesignal-deploy
   $env:AWS_PROFILE = "securesignal-deploy"
   aws sts get-caller-identity
   ```

`aws sts get-caller-identity` must return an IAM user or assumed-role ARN, not
`arn:aws:iam::<account-id>:root`.

The deployment script refuses root credentials.
