#!/usr/local/bin/python3
'''
This script deletes old BuildKit cache from GitLab registry.
It's meant to be run by CI as it uses the job token.

Requires CI_API_V4_URL, CI_PROJECT_ID and CI_JOB_TOKEN to be set.
Requires CACHE_REPOSITORIES to contain comma-separated list of cache repositories.
'''
import os
import requests

def append_names(names, data):
    """Appends names from JSON to name list"""
    if not isinstance(data, list):
        print(f"Warning: Expected list but got {type(data).__name__}: {data}")
        return
    for record in data:
        if isinstance(record, dict) and 'name' in record:
            names.append(record['name'])

def can_tag_be_deleted(branches, branch_name):
    """Checks if a tag should be deleted."""
    for branch in branches:
        if branch['name'] == branch_name and not branch['merged']:
            return False
    return True

def process_cache_registry_repository(gitlab_api_url, project_id,
                                      repository_id, job_token, branches):
    """Processes cache registry repositories"""
    repository_url = f"{gitlab_api_url}/projects/{project_id}/registry/repositories/{repository_id}"
    tags_url = f"{repository_url}/tags"
    auth_headers = {'JOB-TOKEN': job_token}

    page_num = 1
    params = {
        'per_page': 100,
        'page': page_num
    }

    image_tags = []

    print(f"Fetching first page of tag list for repository ID {repository_id}.")
    response = requests.get(url=tags_url, params=params, headers=auth_headers, timeout=60)
    if response.status_code != 200:
        print(f"Error fetching tags: {response.status_code} - {response.text}")
        return
    data = response.json()
    resp_headers = response.headers
    total_pages = int(resp_headers.get('x-total-pages', 1) if data else 0)
    print(f"Total pages available: {total_pages}.")

    append_names(image_tags, data)

    while page_num < total_pages:
        page_num += 1
        params['page'] = page_num
        print(f"Fetching page {page_num} of tag list.")
        response = requests.get(url=tags_url, params=params, headers=auth_headers, timeout=60)
        if response.status_code != 200:
            print(f"Error fetching tags page {page_num}: {response.status_code}")
            continue
        data = response.json()
        append_names(image_tags, data)

    length=len(image_tags)
    print(f"Found total {length} image tags.")

    for image_tag in image_tags:
        if image_tag == "latest":
            continue
        branch_name_1 = image_tag
        branch_name_2 = image_tag.replace("-", "/", 1)
        if can_tag_be_deleted(branches, branch_name_1) and can_tag_be_deleted(branches, branch_name_2):
            print(f"Deleting cache tag {image_tag}")
            response = requests.delete(f"{tags_url}/{image_tag}", headers={'JOB-TOKEN': job_token}, timeout=60)
            response_json=response.json()
            print(f"Tag deletion request finished with code {response.status_code} and response {response_json}")

    print(f"Finished tag processing for repository ID {repository_id}.")

def main():
    """Main function"""
    gitlab_api_url = os.environ['CI_API_V4_URL']
    project_id = os.environ['CI_PROJECT_ID']
    job_token = os.environ['CI_JOB_TOKEN']
    cache_repositories = os.environ['CACHE_REPOSITORIES'].split(',')
    cache_repository_ids = []
    auth_headers = {'JOB-TOKEN': job_token}

    print("Fetching cache registry repositories list.")
    repositories_url = f"{gitlab_api_url}/projects/{project_id}/registry/repositories"
    response = requests.get(url=repositories_url, headers=auth_headers, timeout=60)
    if response.status_code != 200:
        print(f"Error fetching repositories: {response.status_code} - {response.text}")
        return
    data = response.json()
    if not isinstance(data, list):
        print(f"Error: Expected list of repositories but got: {data}")
        return
    for repo in data:
        if isinstance(repo, dict) and repo.get('name') in cache_repositories:
            cache_repository_ids.append(repo['id'])

    if not cache_repository_ids:
        print("No cache repositories found to process.")
        return

    print("Fetching first page of branch list.")
    page_num = 1
    params = {
        'per_page': 100,
        'page': page_num
    }
    branches_url = f"{gitlab_api_url}/projects/{project_id}/repository/branches"
    response = requests.get(url=branches_url, params=params, headers=auth_headers, timeout=60)
    if response.status_code != 200:
        print(f"Error fetching branches: {response.status_code} - {response.text}")
        return
    data = response.json()
    if not isinstance(data, list):
        print(f"Error: Expected list of branches but got: {data}")
        return
    resp_headers = response.headers
    total_pages = int(resp_headers.get('x-total-pages', 1))
    print(f"Total pages available: {total_pages}.")

    branches = data.copy()

    while page_num < total_pages:
        page_num += 1
        params['page'] = page_num
        print(f"Fetching page {page_num} of branch list.")
        response = requests.get(url=branches_url, params=params, headers=auth_headers, timeout=60)
        if response.status_code != 200:
            print(f"Error fetching branches page {page_num}: {response.status_code}")
            continue
        data = response.json()
        if isinstance(data, list):
            branches.extend(data)

    length = len(branches)
    print(f"Found total {length} branches.")

    for repository_id in cache_repository_ids:
        process_cache_registry_repository(gitlab_api_url, project_id, repository_id, job_token, branches)

if __name__ == "__main__":
    main()
