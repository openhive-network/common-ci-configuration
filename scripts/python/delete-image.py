# pylint: disable=C0103
'''
This script deletes a tag from GitLab's embedded docker repository.
Run 'python delete-image.py -h' to see usage instructions.
'''
import argparse
import gitlab

parser = argparse.ArgumentParser(
    description = 'Deletes a tag from GitLab\'s embedded Docker repository')
parser.add_argument('--url',
    default='https://gitlab.syncad.com',
    help='GitLab instance URL (defaults to https://gitlab.syncad.com)')
parser.add_argument('token',
    help='Access token for authentication')
parser.add_argument('project-id',
    help='Project ID (eg. 121)')
parser.add_argument('repository',
    help='Repository location (eg. registry.gitlab.com/hive/haf/data)')
parser.add_argument('tag',
    help='Tag to be deleted (eg. latest)')

args = parser.parse_args()

server_url = args.url
token = args.token
project_id = getattr(args, 'project-id')
repository_location = args.repository
tag_name = args.tag

gl = gitlab.Gitlab(server_url, private_token=token)

def delete_tag(repository):
    '''Deletes tag from repository'''
    try:
        tag = repository.tags.get(id=tag_name)
        tag.delete()
        print(f'Deleted tag \'{tag_name}\' from repository \'{repository_location}\'')
    except gitlab.exceptions.GitlabGetError:
        print(f'Tag \'{tag_name}\' does not exists in repository \'{repository_location}\'')

def find_repository_and_delete_tag():
    '''Looks up repository and invokes 'delete_tag' method on it'''
    try:
        repository_found = False
        project = gl.projects.get(project_id)
        repositories = project.repositories.list(all=True)

        for repository in repositories:
            if repository_location == repository.location:
                repository_found = True
                delete_tag(repository)

        if not repository_found:
            print(f'Registry repository \'{repository_location}\' ' +
                'was not found in project \'{project.name}\'')
    except gitlab.exceptions.GitlabGetError:
        print(f'Project with ID \'{project_id}\' was not found in GitLab instance \'{server_url}\'')

find_repository_and_delete_tag()
