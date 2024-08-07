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

import os
import re
import logging
from typing import Optional

from google.api_core.client_options import ClientOptions
from google.api_core.exceptions import InternalServerError
from google.api_core.exceptions import RetryError
from google.cloud import documentai  
from google.cloud import storage


# Retrieve Job-defined env vars
TASK_INDEX = os.getenv("CLOUD_RUN_TASK_INDEX", 0)
TASK_ATTEMPT = os.getenv("CLOUD_RUN_TASK_ATTEMPT", 0)
# Retrieve User-defined env vars
PROJECT_ID = os.getenv("PROJECT_ID")
LOCATION = os.getenv("LOCATION") # Example: - "us"
PROCESSOR_ID = os.getenv("PROCESSOR_ID") # Example: - ac27785bf4bee278
GCS_OUTPUT_PREFIX = os.getenv("GCS_OUTPUT_PREFIX") # Must end with a trailing slash `/`. Format: gs://bucket/directory/subdirectory/
GCS_INPUT_PREFIX = os.getenv("GCS_INPUT_PREFIX") # Example: - "gs://doc-ai-processor/input-forms/" # Format: gs://bucket/directory/


def batch_process_documents(
    project_id: str = None,
    location: str = None,
    processor_id: str = None,
    gcs_output_uri: str = None,
    gcs_input_prefix: Optional[str] = None,
    field_mask: Optional[str] = None,
    timeout: int = 400,
) -> None:

  """Program that processes documents with forms stored in a GCS bucket and converts into JSON.

    Args:
        project_id: project id where solution is deployed,
        location: location of the form Document AI form processor,
        processor_id: Processor Id of Document AI form processor,
        gcs_output_uri: GCS directory to store the out json files,
        gcs_input_prefix: GCS directory to store input files to be processed 
    """
  # Set the `api_endpoint` if you use a location other than "us".
  opts = ClientOptions(api_endpoint=f"{location}-documentai.googleapis.com")

  client = documentai.DocumentProcessorServiceClient(client_options=opts)

  # Specify a GCS URI Prefix to process an entire directory
  gcs_prefix = documentai.GcsPrefix(gcs_uri_prefix=gcs_input_prefix)
  input_config = documentai.BatchDocumentsInputConfig(gcs_prefix=gcs_prefix)

  # Cloud Storage URI for the Output Directory
  gcs_output_config = documentai.DocumentOutputConfig.GcsOutputConfig(
    gcs_uri=gcs_output_uri, field_mask=field_mask
  )

  # Where to write results
  output_config = documentai.DocumentOutputConfig(gcs_output_config=gcs_output_config)

  # The full resource name of the processor, e.g.:
  # projects/{project_id}/locations/{location}/processors/{processor_id}
  name = client.processor_path(project_id, location, processor_id)

  request = documentai.BatchProcessRequest(
    name=name,
    input_documents=input_config,
    document_output_config=output_config,
  )

  # BatchProcess returns a Long Running Operation (LRO)
  operation = client.batch_process_documents(request)

  # Continually polls the operation until it is complete.
  # This could take some time for larger files
  # Format: projects/{project_id}/locations/{location}/operations/{operation_id}
  try:
    logging.info(f"Waiting for operation {operation.operation.name} to complete...")
    operation.result(timeout=timeout)
  # Catch exception when operation doesn't finish before timeout
  except (RetryError, InternalServerError) as e:
    logging.error(e.message)

  # Once the operation is complete,
  # get output document information from operation metadata
  metadata = documentai.BatchProcessMetadata(operation.metadata)

  if metadata.state != documentai.BatchProcessMetadata.State.SUCCEEDED:
    raise ValueError(f"Batch Process Failed: {metadata.state_message}")

  storage_client = storage.Client()

  logging.info("Output files:")
  # One process per Input Document
  for process in list(metadata.individual_process_statuses):
    # output_gcs_destination format: gs://BUCKET/PREFIX/OPERATION_NUMBER/INPUT_FILE_NUMBER/
    # The Cloud Storage API requires the bucket name and URI prefix separately
    matches = re.match(r"gs://(.*?)/(.*)", process.output_gcs_destination)
    if not matches:
      logging.error(
        "Could not parse output GCS destination:",
        process.output_gcs_destination,
      )
      continue

    output_bucket, output_prefix = matches.groups()

    # Get List of Document Objects from the Output Bucket
    output_blobs = storage_client.list_blobs(output_bucket, prefix=output_prefix)

    # Document AI may output multiple JSON files per source file
    for blob in output_blobs:
      # Document AI should only output JSON files to GCS
      if blob.content_type != "application/json":
        logging.info(
          f"Skipping non-supported file: {blob.name} - Mimetype: {blob.content_type}"
        )
        continue

      # Download JSON File as bytes object and convert to Document Object
      logging.info(f"Fetching {blob.name}")
      document = documentai.Document.from_json(
        blob.download_as_bytes(), ignore_unknown_fields=True
      )

      # Read the text recognition output from the processor @TODO update log level to debug
      print("The document contains the following text:")
      print(document.text)
     

# Start script
if __name__ == "__main__":
  logging.info(f"Starting Task #{TASK_INDEX}, Attempt #{TASK_ATTEMPT}...")
  batch_process_documents(project_id=PROJECT_ID,
                          location=LOCATION,
                          processor_id=PROCESSOR_ID,
                          gcs_output_uri=GCS_OUTPUT_PREFIX,
                          gcs_input_prefix=GCS_INPUT_PREFIX)
  name = os.environ.get("NAME", "World")
  logging.info(f"Completed Task #{TASK_INDEX}.")



