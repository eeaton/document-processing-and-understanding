# Copyright 2024 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


locals {
  registry_url = "${var.repository_region}-docker.pkg.dev/${var.project_id}/${var.artifact_repo_name}"
  cloud_build_fileset = [
    "${path.module}/build/cloudbuild.yaml.template",
    "${path.module}/build/cloudbuild.yaml",
    "${path.module}/build/Dockerfile",
    "${path.module}/build/main.py",
    "${path.module}/build/requirements.in",
  ]
  cloud_build_content_hash = sha512(join("", [for f in local.cloud_build_fileset : fileexists(f) ? filesha512(f) :
    sha512("file-not-found")]))
  service_account_name     = var.classifier_cloud_run_job_name
}

# Enable APIs
# See https://github.com/terraform-google-modules/terraform-google-project-factory
# The modules/project_services
module "project_services" {
  source                      = "terraform-google-modules/project-factory/google//modules/project_services"
  version                     = "14.5.0"
  project_id                  = var.project_id
  disable_services_on_destroy = false
  disable_dependent_services  = false
  activate_apis = [
    # General container build and registry
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "documentai.googleapis.com",
    "run.googleapis.com",
    "compute.googleapis.com",
  ]

  # Provide more access to the cloudbuild service account
  activate_api_identities = [{
    "api" : "cloudbuild.googleapis.com",
    "roles" : [
      "roles/run.admin",
      # Required for Cloud Run to launch as the normal compute service account
      "roles/iam.serviceAccountUser",
    ]
    },
    {
      "api" : "pubsub.googleapis.com",
      # PubSub publish to Cloud Run
      "roles" : [
        #"roles/iam.serviceAccountUser",
        "roles/iam.serviceAccountTokenCreator",
      ],
    }
  ]
}

module "doc_classifier_account" {
  source     = "terraform-google-modules/service-accounts/google"
  version    = "~> 4.2"
  project_id = var.project_id
  prefix     = "dpu"
  names      = [local.service_account_name]
  project_roles = [
    "${var.project_id}=>roles/documentai.apiUser",
  ]
  display_name = "Doc Classifier Account"
  description  = "Account used to run the document classifier jobs"
}

# Depends on: input bucket, artefactory (registury_url), and docclassifier service account
resource "local_file" "cloudbuild_cloud_run" {
  filename = "${path.module}/build/cloudbuild.yaml"
  content = templatefile("${path.module}/build/cloudbuild.yaml.template", {
    project_id            = var.project_id,
    registry_url          = local.registry_url,
    region                = var.region,
    service_account       = module.doc_classifier_account.email
    classifier_cloud_run_job_name    = var.classifier_cloud_run_job_name,
    build_service_account = var.cloud_build_service_account_email
  })
}

# See https://github.com/terraform-google-modules/terraform-google-gcloud
module "gcloud" {
  source  = "terraform-google-modules/gcloud/google"
  version = "~> 3.4"

  create_cmd_entrypoint = "gcloud"
  create_cmd_body       = "builds submit --region ${var.region} --project ${var.project_id} --config \"${local_file.cloudbuild_cloud_run.filename}\" \"${path.module}/../../..\""
  enabled               = true

  create_cmd_triggers = {
    source_contents_hash = local.cloud_build_content_hash
  }
}