import json
import docker
from docker.errors import ImageNotFound, APIError


def get_dockerhub_image_uri(uid, dockerhub_username, repo_name=""):
    repo_base, repo_name_only = repo_name.lower().split("/")
    hsh = uid.replace("instance_", "")

    if uid == "instance_element-hq__element-web-ec0f940ef0e8e3b61078f145f34dc40d1938e6c5-vnan":
        repo_name_only = 'element-web'  # Keep full name for this one case
    elif 'element-hq' in repo_name.lower() and 'element-web' in repo_name.lower():
        repo_name_only = 'element'
        if hsh.endswith('-vnan'):
            hsh = hsh[:-5]
    # All other repos: strip -vnan suffix
    elif hsh.endswith('-vnan'):
        hsh = hsh[:-5]
    
    tag = f"{repo_base}.{repo_name_only}-{hsh}"
    if len(tag) > 128:
        tag = tag[:128]
    
    return f"{dockerhub_username}/sweap-images:{tag}"

def verify_image_exists(image_name):
    """Verify if a Docker image exists on DockerHub using Docker API."""
    client = docker.from_env()
    try:
        # Try to inspect the image remotely (this will check DockerHub)
        client.images.get_registry_data(image_name)
        print(f"✓ Image exists: {image_name}")
        return True
    except ImageNotFound:
        raise ValueError(f"✗ Image NOT found: {image_name}")
    except APIError as e:
        raise ValueError(f"⚠ API error checking {image_name}: {e}")
    except Exception as e:
        raise ValueError(f"⚠ Unexpected error checking {image_name}: {e}")

def run_batch(instances):
    for idx in range(len(instances)):
        instances[idx]["image"] = get_dockerhub_image_uri(
            instances[idx]["instance_id"],
            "jefzda",
            instances[idx]["repo"]
        )
        # Verify whether instances[idx]["image"] exists on dockerhub
        # verify_image_exists(instances[idx]["image"])

#with open("/home/v-kenanli/workspace/ablation/data/pro/pyt/original_pro.jsonl") as f:
#    l = [json.loads(i) for i in f]
#    run_batch(l)
#with open("/home/v-kenanli/workspace/ablation/data/pro/pyt/original_pro.jsonl", "w") as f:
#    for i in l:
#        f.write(json.dumps(i)+"\n")

with open("/home/v-kenanli/workspace/ablation/data/pro/go/original_pro.jsonl") as f:
    l = [json.loads(i) for i in f]
    run_batch(l)
with open("/home/v-kenanli/workspace/ablation/data/pro/go/original_pro.jsonl", "w") as f:
    for i in l:
        f.write(json.dumps(i)+"\n")


with open("/home/v-kenanli/workspace/ablation/data/pro/node/original_pro.jsonl") as f:
    l = [json.loads(i) for i in f]
    run_batch(l)
with open("/home/v-kenanli/workspace/ablation/data/pro/node/original_pro.jsonl", "w") as f:
    for i in l:
        f.write(json.dumps(i)+"\n")