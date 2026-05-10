# Update Github actions ENV Vars and Secret Vars
Add the required vars and secrets in Repo → Settings → Secrets and variables → Actions
* DOCKERHUB_TOKEN

# Run the image locally
```
docker run -p 5000:5000 roshaneishara/argo-canary-demo-app:latest
docker run -p 5000:5000 roshaneishara/argo-canary-demo-app:latest

curl http://localhost:5000/
curl http://localhost:5000/health
curl http://localhost:5000/error
```


