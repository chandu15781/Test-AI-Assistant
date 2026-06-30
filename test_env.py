import os
from dotenv import load_dotenv

load_dotenv('.env', override=True)
print(f'JENKINS_URL: {os.environ.get("JENKINS_URL")}')
print(f'JIRA_URL: {os.environ.get("JIRA_URL")}')
print(f'POLARION_URL: {os.environ.get("POLARION_URL")}')
