# BIDSIF_VM


## Step 1
```bash
cd /home/crpn/GITLAB/bidsif_vm
```

## Step 2
```bash
source .bidsif_vm_venv/bin/activate
```
## Step 3
```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Step 4 (temporary solution)

On you computer
```bash
ssh -L 8000:localhost:8000 crpn@10.184.12.152
```

Then open your browser and go to http://localhost:8000/

## Step 5

For the UNC path

add:
```bash
//smb-crpn-isi-stj.univ-amu.fr/crpn$/USers/your_directory
``` 

For subpath to dataset:

Example:
```bash
relative_path/to/dataset/from/home
```

Username and password are your usual AMU credentials.

---

Important note: It is required to have the ```bids_configurator.txt``` filled and placed in your dataset folder for the BIDSIF_VM to work.

Follow [BIDSIF repo](https://gitlab.crpn.univ-amu.fr/di-s-c/bidsif) for instructions on fillin gin the ```bids_configurator.txt```.
