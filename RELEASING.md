## Releasing

First, this is an excellent resource: https://packaging.python.org/tutorials/packaging-projects/

- Make sure everything is ready to be released to the registry
- Build the SDK: `yarn build` (this runs a python command)
- Upload the SDK: `python -m twine upload dist/` (you can find the creds in 1pass)
