# AWS terms used in MessQry AI

This guide explains the AWS words used in the project and shows how the parts
fit together. It distinguishes selected project choices from services that are
still recommendations.

## Does the database come with the app server?

No. The app's compute and its database are separate resources, even when both
are hosted by AWS. Think of compute as the kitchen that does the work and the
database as a separate filing room that keeps records. You create and configure
each resource, then allow the app to connect to the database over a private
network.

MessQry's code is designed to use PostgreSQL in a shared environment. Amazon
RDS for PostgreSQL is a reasonable managed hosting choice: AWS operates the
database service while MessQry still needs its own database, network access,
login, connection settings, and migration process. RDS has **not** yet been
selected or created for this project. Until that decision is made, don't treat
an RDS endpoint or credentials as existing project configuration.

## How the planned pieces fit

```text
Browser
  └─ MessQry web console
       └─ HTTPS API (hosting choice still open)
            ├─ PostgreSQL database (AWS RDS is a recommendation, not selected)
            ├─ S3 bucket for model package files
            └─ ECS control service
                 └─ Fargate task for an isolated model runtime
```

The browser calls the API. It does not connect directly to PostgreSQL, S3, or a
model task. The API stores structured records in PostgreSQL and model package
files in S3. When a prediction is requested, the API routes the bounded request
to the selected private model runtime. The frontend hosting path and API
hosting service are still open architecture choices.

## AWS terms in this repository

| Term | Plain-language meaning | MessQry use and status |
| --- | --- | --- |
| **AWS Region** | A geographic area where AWS runs services. | Must be chosen before cloud resources are created. |
| **Availability Zone (AZ)** | A separate data-center area inside a Region. | Relevant to database and service availability; the deployment layout is not selected yet. |
| **EC2** | A virtual computer rented from AWS, where you manage the operating system and software. | EC2 is not the selected model-serving approach. The control API hosting choice remains open. |
| **Container** | A packaged application with its required files and libraries. | MessQry packages model runtimes as containers. |
| **ECS** | AWS service that starts, stops, and tracks containers. | Selected to manage model runtime tasks. |
| **Fargate** | AWS compute that runs a container without you managing the underlying virtual machines. | Selected for one isolated model task per deployment. The task size and idle behavior are prototype settings, not a production guarantee. |
| **Task definition** | A recipe describing a container, CPU, memory, networking, and permissions. | The runtime launcher expects a configured ECS task definition; it has not been created in AWS. |
| **ECR** | A private registry for storing container images. | A likely place for MessQry images, but not yet selected in the infrastructure plan. |
| **RDS for PostgreSQL** | Managed PostgreSQL hosting. | Recommended candidate for the production metadata database; not yet selected or provisioned. |
| **Database endpoint** | A network address and port clients use to reach a database. | Would be placed in `MESSQRY_DATABASE_URL` through managed configuration. It is different from a MessQry HTTP API endpoint. |
| **S3 bucket / object** | A bucket is a container; an object is a stored file. | Selected for private model package storage. A real bucket and policy are not configured yet. |
| **VPC** | A private network boundary in an AWS account. | Database and compute should communicate through planned private networking. The VPC is not configured yet. |
| **Subnet** | A smaller network range inside a VPC. | The runtime launcher expects private subnet IDs. Their routes and access are not verified. |
| **Security group** | A stateful network firewall attached to resources. | Rules must permit only the required connections, such as API-to-database and control-plane-to-runtime traffic. IDs and rules are not configured yet. |
| **IAM role** | An identity assigned to an AWS service or task. | Preferred way for API/runtime tasks to receive AWS permissions without storing long-lived AWS keys in the application. Exact roles and policies remain unconfigured. |
| **IAM policy** | A list of AWS actions and resources an identity may use. | Should give each workload only the S3/ECS/log permissions it needs. |
| **Cognito User Pool** | AWS service that manages user sign-in and issues tokens. | Selected as the planned OIDC identity provider; pool, app client, and scopes are not provisioned yet. |
| **Secrets Manager** | Service for storing and retrieving application secrets. | A managed secret store is planned; the provider choice and secret values are not configured. |
| **CloudWatch** | AWS monitoring and logging services. | A possible destination for AWS logs/metrics, not yet selected as MessQry's telemetry design. |
| **Endpoint (general)** | A named address where a service can be reached. | Be specific: *API URL*, *database hostname*, *S3 service endpoint*, and *private runtime IP* are different things. |
| **Migration** | A controlled change to database tables and constraints. | Alembic migration files change MessQry's schema; they must be run against the selected database during release. |

## What “integrate the database separately” means

When a database is hosted on RDS, these pieces need to agree:

1. Create a PostgreSQL database in the chosen AWS Region and VPC.
2. Give the API's hosting location network permission to reach the database port
   (normally PostgreSQL port `5432`) using private subnets and security-group
   rules. Keep the database private; the browser should never connect to it.
3. Create a database/user for MessQry and store its password in the chosen
   secrets service. The application receives a connection string via
   `MESSQRY_DATABASE_URL`, for example
   `postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE`.
4. Install the repository's optional PostgreSQL driver and run Alembic
   migrations so the database has the tables and indexes the code expects.
5. Verify TLS, backups, restore, connection limits, and migration behavior before
   treating the database as production-ready.

The exact RDS size, backup window, encryption key, availability setup, and
credentials still need to be chosen. Don't put passwords or AWS access keys in
Git, frontend variables, or model artifacts. AWS workloads should use scoped
IAM roles for AWS service access; the database login is separate and belongs in
managed secret configuration.

## Three kinds of “credentials”

- **AWS identity credentials:** identify a person or workload to AWS. In the
  deployed app, ECS task IAM roles are the intended workload identity; avoid
  permanent access keys inside the app.
- **Database credentials:** username/password (or another supported database
  authentication method) used by FastAPI to log in to PostgreSQL. Store these
  outside source control and restrict them to the app database.
- **MessQry user/service credentials:** OIDC access tokens used to call the
  MessQry API. Cognito signs users in; machine clients receive only the
  application scopes they need. These tokens do not replace AWS IAM or database
  credentials.

## Useful sources

- [Amazon RDS for PostgreSQL](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/CHAP_PostgreSQL.html)
- [Amazon ECS and Fargate](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/)
- [Amazon S3 overview](https://docs.aws.amazon.com/AmazonS3/latest/userguide/Welcome.html)
