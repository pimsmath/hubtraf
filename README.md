# hubtraf: Traffic simulator for JupyterHub

## License

See [LICENCE](LICENCE).

## Geting started with the Python module

Install the latest version with
```shell
pip install git+https://github.com/pimsmath/hubtraf.git
```

For basic usage, see the [docs](docs/index.rst)

For scripting abilities refer to [hubtraf/\_\_main\_\_.py](hubtraf/__main__.py)

## Chartpress
Install chartpress in a venv and activate it
```bash
$ python3 -m venv .
$ source bin/activate
$ python3 -m pip install -r dev-requirements.txt
```

When you make changes to the images directory, commit them and run chartpress
```bash
# To build new images and update Chart.yaml/values.yaml tags
$ chartpress

# To push tagged images to Dockerhub
$ chartpress --push

# To publish the repository to our helm repository
$ chartpress --publish
```
