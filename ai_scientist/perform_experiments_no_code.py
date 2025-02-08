# for no code templates, we perform "investigations" rather than code experiments.

import json
import os.path as osp
import shutil
import subprocess
import sys
from subprocess import TimeoutExpired

from ai_scientist.generate_ideas_no_code import search_for_papers

MAX_ITERS = 4 # for failed queries to get a certain data object
# MAX_ITERS = 1
# MAX_RUNS = 5
MAX_QUERIES = 10 # max num of different data objects to gather
MAX_STDERR_OUTPUT = 1500

# coder_prompt = """Your goal is to investigate the following idea: {title}.
# The proposed investigation is as follows: {idea}.
# You are given a total of up to {max_queries} research queries to complete the investigation. You do not need to use all {max_queries}.

# First, plan the list of data objects you would like to gather. Modify and duplicate the example in `investigation.json` for each query, changing only the `"Description"` field to describe the contents of the `"Data"` object you would like to have.
# For example, a data object can be table of yearly historical data, or simply a single fact about the topic.

# We will use your changes to gather the data needed and populate each data object in `investigation.json` with the results.
# """
coder_prompt = """Your goal is to investigate the following idea: {title}.
The proposed investigation is as follows: {idea}.
You are given a total of up to {max_queries} research queries to complete the investigation. You do not need to use all {max_queries}.

First, plan the list of data objects you would like to gather.
Modify and duplicate the example in `investigation.json` for each query.
Change the `"Description"` field to describe the contents of the `"Data"` object you would like to have.
Change the `"Exists"` and `"Source"` fields to indicate if the data should already exist (e.g., from a prior study or on the internet) or if it needs to be gathered from scratch as during this proposed research.
Change the `"Phase"` and `"Purpose"` fields to explain if it is needed for the proposal justification (e.g., data showing the scale or impact of the problem, examples of similar successful research in other contexts, other statistics), or if it is needed for the actual investigation (existing data or new data collection that needs to be sourced by the researcher but can't be done in just this proposal).
Change the `"Query"` field to a query to search the literature with for the data ONLY if it exists and is needed for the proposal.
Leave the other fields empty.
For example, a data object can be table of yearly historical data, or simply a single fact from an article about the topic.
These will represent the content of your investigation proposal, e.g., for supporting the importance of the topic and for laying out the research plan of the actual investigation if it were to be conducted.
"""


# RUN EXPERIMENT
def gather_data(folder_name, run_num, timeout=7200):
    # cwd = osp.abspath(folder_name)
    # # COPY CODE SO WE CAN SEE IT.
    # shutil.copy(
    #     osp.join(folder_name, "investigation.json"),
    #     osp.join(folder_name, f"run_{run_num}.json"),
    # )

    # try:
    #     result = subprocess.run(
    #         command, cwd=cwd, stderr=subprocess.PIPE, text=True, timeout=timeout
    #     )

    #     if result.stderr:
    #         print(result.stderr, file=sys.stderr)

    #     if result.returncode != 0:
    #         print(f"Run {run_num} failed with return code {result.returncode}")
    #         if osp.exists(osp.join(cwd, f"run_{run_num}")):
    #             shutil.rmtree(osp.join(cwd, f"run_{run_num}"))
    #         print(f"Run failed with the following error {result.stderr}")
    #         stderr_output = result.stderr
    #         if len(stderr_output) > MAX_STDERR_OUTPUT:
    #             stderr_output = "..." + stderr_output[-MAX_STDERR_OUTPUT:]
    #         next_prompt = f"Run failed with the following error {stderr_output}"
    #     else: # success
    #         with open(osp.join(cwd, f"run_{run_num}", "final_info.json"), "r") as f:
    #             results = json.load(f)
    #         results = {k: v["means"] for k, v in results.items()}

    #         next_prompt = f"""Run {run_num} completed. Here are the results:
# {results}

# Decide if you need to re-plan your experiments given the result (you often will not need to).

# Someone else will be using `notes.txt` to perform a writeup on this in the future.
# Please include *all* relevant information for the writeup on Run {run_num}, including an experiment description and the run number. Be as verbose as necessary.

# Then, implement the next thing on your list.
# We will then run the command `python experiment.py --out_dir=run_{run_num + 1}'.
# YOUR PROPOSED CHANGE MUST USE THIS COMMAND FORMAT, DO NOT ADD ADDITIONAL COMMAND LINE ARGS.
# If you are finished with experiments, respond with 'ALL_COMPLETED'."""
    #     return result.returncode, next_prompt
    # except TimeoutExpired:
    #     print(f"Run {run_num} timed out after {timeout} seconds")
    #     if osp.exists(osp.join(cwd, f"run_{run_num}")):
    #         shutil.rmtree(osp.join(cwd, f"run_{run_num}"))
    #     next_prompt = f"Run timed out after {timeout} seconds"
    #     return 1, next_prompt


# # RUN PLOTTING
# def run_plotting(folder_name, timeout=600):
#     cwd = osp.abspath(folder_name)
#     # LAUNCH COMMAND
#     command = [
#         "python",
#         "plot.py",
#     ]
#     try:
#         result = subprocess.run(
#             command, cwd=cwd, stderr=subprocess.PIPE, text=True, timeout=timeout
#         )

#         if result.stderr:
#             print(result.stderr, file=sys.stderr)

#         if result.returncode != 0:
#             print(f"Plotting failed with return code {result.returncode}")
#             next_prompt = f"Plotting failed with the following error {result.stderr}"
#         else:
#             next_prompt = ""
#         return result.returncode, next_prompt
#     except TimeoutExpired:
#         print(f"Plotting timed out after {timeout} seconds")
#         next_prompt = f"Plotting timed out after {timeout} seconds"
#         return 1, next_prompt


# PERFORM INVESTIGATION
def perform_investigation(idea, folder_name, coder, client, client_model) -> bool:
    ## RUN EXPERIMENT
    current_iter = 0
    run = 1
    next_prompt = coder_prompt.format(
        title=idea["Title"],
        idea=idea["Description"],
        max_queries=MAX_QUERIES
    )

    # 1. What data objects to gather
    coder_out = coder.run(next_prompt)
    print(coder_out)

    # 2. Gather the data for the proposal justification
    # when data gathering fails for a certain data object, we retry up to MAX_ITERS times.
    # or: just leave blank, and in writeup it can note that this data was not found for claims that rely on it?

#     while run < MAX_QUERIES + 1:
#         if current_iter >= MAX_ITERS:
#             print("Max iterations reached")
#             break
#         coder_out = coder.run(next_prompt)
#         print(coder_out)
#         if "ALL_COMPLETED" in coder_out:
#             break
#         return_code, next_prompt = gather_data(folder_name, run)
#         if return_code == 0:
#             run += 1
#             current_iter = 0
#         current_iter += 1
#     if current_iter >= MAX_ITERS:
#         print("Not all experiments completed.")
#         return False

#     current_iter = 0
#     next_prompt = """
# Great job! Please modify `plot.py` to generate the most relevant plots for the final writeup. 

# In particular, be sure to fill in the "labels" dictionary with the correct names for each run that you want to plot.

# Only the runs in the `labels` dictionary will be plotted, so make sure to include all relevant runs.

# We will be running the command `python plot.py` to generate the plots.
# """
#     while True:
#         _ = coder.run(next_prompt)
#         return_code, next_prompt = run_plotting(folder_name)
#         current_iter += 1
#         if return_code == 0 or current_iter >= MAX_ITERS:
#             break
#     next_prompt = """
# Please modify `notes.txt` with a description of what each plot shows along with the filename of the figure. Please do so in-depth.

# Somebody else will be using `notes.txt` to write a report on this in the future.
# """
#     coder.run(next_prompt)

#     return True
