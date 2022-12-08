# Common CI Configuration

This project contains the common CI templates and scripts for Hive and Hive-related projects.

## Directory structure

- misc - miscellaneous files
- scripts/bash - Bash scripts
- scripts/python - Python scripts
- templates - GitLab CI templates

## Job templates

 - [data_image_jobs.gitlab-ci.yml](templates/data_image_jobs.gitlab-ci.yml) - templates for performing docker builds
 - [test_jobs.gitlab-ci.yml](templates/test_jobs.gitlab-ci.yml) - templates for running tests
## Example jobs

The GitLab CI configuration for this repository contains example jobs based on the templates defined in it. On top of that the Docker images are built by jobs also based on said templates.

Note that jobs **prepare_example_hived_data_5m_image** and **prepare_example_haf_data_5m_image** are configured to use Docker image registry belonging to this project, rather than one belonging to hive/hive> and hive/haf> projects respectively. This does not work out of the box, beacuse the jobs require certain prebuilt images. Those images need to be either built manually or pulled from hive/hive> and hive/haf> and then pushed to whatever custom registry you'd like to use. The credentials required by those jobs are the same you'd use with `docker login` command. Not that the **before_script** sections in those jobs are only necessary since this repository defines neither hive/hive> nor hive/haf> as submodules.

The password required by the **example_hived_data_image_cleanup** and **example_haf_data_image_cleanup** is either personal, group or project access token with permission to use GitLab API.

Example JMeter benchmark job shows how to expose the JMeter result dashboard via GitLab pages.
For the example job they are available at https://hive.pages.syncad.com/-/common-ci-configuration/-/jobs/<job-id>/artifacts/web_jmeter_report/index.html

## Miscellaneous files

Currently available miscellaneous files are [checkstyle2junit.xslt](misc/checkstyle2junit.xslt), [docker-compose.dind.yml](misc/docker-compose.dind.yml) and [example-jmeter-test.jmx](misc/example-jmeter-test.jmx).

`checkstyle2junit.xslt` is an XSL transformation file, which can be used to transform checkstyle-style test reports into junit-style ones. You can see how to use it in job **lint_bash_scripts**.

`docker-compose.dind.yml` is a Compose file for setting up a simple Docker-in-Docker container. To connect to that container with a Docker CLI one use command like:

```bash
docker run -it --rm --network docker -e DOCKER_TLS_CERTDIR=/certs -v docker-certs-client:/certs/client:ro --name docker-cli docker:20.10.21
```

The iportant bit is connecting to the right network (`--network docker`) and mounting the TLS certificates (`-e DOCKER_TLS_CERTDIR=/certs -v docker-certs-client:/certs/client:ro`). Other parameters can be changed to suit your purposes.

`example-jmeter-test.jmx` is an example Jmeter test file used in the exmaple Jmeter job. It shows some basic test configuration as well as how to configure creation of the XML result file for m2u.
