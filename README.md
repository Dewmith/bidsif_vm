# BIDSIF_VM

## Introduction

**BIDSIF_VM** is a virtual-machine–based web interface designed to simplify and standardize the conversion of neuroimaging datasets into the **BIDS (Brain Imaging Data Structure)** format.

It provides a lightweight, reproducible environment where users can:
- Configure their dataset using a simple text-based configurator
- Launch a local web interface to connect to the web API of the Virtual Machine.
- Add the dataset path to be BIDSified.
- Run the BIDS conversion algorthmn within the VM and store the BIDSIFied dataset in your DCS storage.

The goal of BIDSIF_VM is to lower the technical barrier to BIDS conversion by providing:
- A preconfigured Python environment on a VM with all necessary dependencies for BIDS conversion.
- The same environment for all users, ensuring consistency and reproducibility without worrying about local setup issues.

This README walks you step by step through connecting to the VM, setting up the environment if needed, launching the API, and accessing the interface from your local machine.

## Step 1 

Make sure you are successfully connected to the vm through SSH and that you are in the directory `/home/crpn
`. 

Verify by typing in `pwd` on the terminal.
  
## Step 2 : Get into the `bidsif_vm` directory.

```bash
cd /home/crpn/GITLAB/bidsif_vm
```
<details>
<summary><h2>Expand and follow the insctructions if the Vitual env is not set</h2></summary>

## Step 2.1 : Create the Virtual environment using UV

```bash
uv venv .bidsif_vm_venv --python 3.10.0
```

## Step 2.2 : Install the dependencies
```bash
uv pip install -r requirements.txt --python .bidsif_vm_venv
```

</details>

## Step 3 : Activate the virtual environment.

```bash
source .bidsif_vm_venv/bin/activate
```
## Step 4 : Run the web interface API.

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Step 5 (temporary solution)

Tunnel the local connection of the VM to your PC to be able to access the web interface through your PC. 

Run the following on your PC terminal.
```bash
ssh -L 8000:localhost:8000 crpn@10.184.12.152
```

Then open your browser and go to http://localhost:8000/

## Step 6

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
