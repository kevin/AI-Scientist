# for no code templates, we perform "investigations" rather than code experiments.

import json
import os.path as osp
import shutil
import subprocess
import sys
from subprocess import TimeoutExpired

from ai_scientist.generate_ideas_no_code import search_for_papers
from ai_scientist.llm import get_response_from_llm, extract_json_between_markers

MAX_ITERS = 4 # for failed queries to get a certain data object
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
Objects needed for the proposal should be limited to what can be found on Semantic Scholar, but otherwise you can propose any data object you think would be useful for the investigation.
These will represent the content of your investigation proposal, e.g., for supporting the importance of the topic and for laying out the research plan of the actual investigation if it were to be conducted.
"""


# RUN INVESTIGATION

# helper
def get_papers(query):
    papers = search_for_papers(query, result_limit=10, engine="semanticscholar")
    if papers is None:
        papers_str = "No papers found."

    paper_strings = []
    for i, paper in enumerate(papers):
        paper_strings.append(
            """{i}: {title}. {authors}. {venue}, {year}.\nNumber of citations: {cites}\nAbstract: {abstract}""".format(
                i=i,
                title=paper["title"],
                authors=paper["authors"],
                venue=paper["venue"],
                year=paper["year"],
                cites=paper["citationCount"],
                abstract=paper["abstract"],
            )
        )
    papers_str = "\n\n".join(paper_strings)
    return papers_str

# prompt for gathering proposal data (not investigation data)
gather_data_system_msg = """You are an ambitious researcher who is looking to publish a paper that will contribute significantly to the field.
You are writing a proposal for a research investigation with the title "{title}".
The proposed investigation is as follows: {idea}.

You need to gather data to justify the importance of the research.
Each data object has a description of what data is needed and a query to search for relevant papers.
You will be shown search results from Semantic Scholar and need to either:

1. Fill in the "Data" field of the object based on information from the papers, if you found relevant data
2. Refine the query to search again, if the current results aren't quite right
3. Modify the data object description to look for different but related information, if the original data cannot be found

You have up to {max_iters} attempts to fill the data object but do not need to use them all.

For option 1, extract specific facts, statistics, or findings from the papers that match what the data object needs. This will exit early.
For option 2, suggest a modified search query that might find more relevant papers.
For option 3, suggest a new data object that might be easier to find in the literature.

The current data object is:
```json
{
    "Description": "{data_description}"
    "Source": "{source}"
    "Purpose": "{purpose}"
    "Query": "{query}"
    "Citation": <empty>
    "Data": <empty>
}
"""

gather_data_prompt = '''Round {current_iter}/{num_iters}.
You have this idea:

The results of the last query are:
"""
{last_query_results}
"""

Respond in the following format:

THOUGHT:
<THOUGHT>

RESPONSE:
```json
<JSON>
```

In <THOUGHT>, first briefly reason over the results and whether they are useful for the data object you are trying to gather.

In <JSON>, respond in JSON format with a new data object containing all the fields from the previous data object.
Option 1: If you found relevant data in the papers, fill in the "Data" field with the relevant information formatted and the "Citation" field with the citation of the paper. You may output an array of citations if you used multiple papers to gather the data.
- This is the only case when you should fill in the "Data" and "Citation" fields.
- You may also make small changes to the other fields if the data found is slightly different from what was originally proposed but still fits the purpose.
Option 2: If you need to refine the query, fill in the "Query" field with a new query to search for papers and leave the other fields unchanged.
Option 3: If you must change the data object description because the original data cannot be found or you deem it unfit, you may change any of the fields except "Data" and "Citation".

A query will work best if you are able to recall the exact name of the paper you are looking for, or the authors.
This JSON will be automatically parsed, so ensure the format is precise.'''

def gather_data(idea, folder_name, client, client_model):
    # when data gathering fails for a certain data object, we retry up to MAX_ITERS times.
    # fails can adjust query or change the data object to a new one if it can't be found
    # for investigation data that can't be found: just leave blank, and in writeup it can note that this data was not found for claims that rely on it

    # read in the investigation.json file
    try:
        with open(osp.join(folder_name, "investigation.json"), "r") as f:
            investigation = json.load(f)
            # make a copy of the original investigation file before we start modifying it
            shutil.copy(
                osp.join(folder_name, "investigation.json"),
                osp.join(folder_name, "investigation_original.json"),
            )
    except FileNotFoundError:
        print("investigation.json not found.")
        return False
    except json.JSONDecodeError:
        print("investigation.json is not a valid json file.")
        return False

    for data_object in investigation:
        # must be possible to gather
        if data_object["Exists"] == "No":
            continue
        # must be needed for the proposal (we don't get investigation data)
        if data_object["Purpose"] == "Investigation":
            continue
        # must have a query (should be filled in by coder if exists)
        assert data_object["Query"] != "", "Query must be filled in for data object that exists and is needed for proposal."

        # search query in Semantic Scholar, and let language model either generate the data object or refine the query
        query = data_object["Query"]
        papers_str = get_papers(query)
        
        try:
            print(f"Gathering data object: {data_object['Description']}")
            gather_data_system_prompt = gather_data_system_msg.format(
                title=idea["Title"],
                idea=idea["Description"],
                max_iters=MAX_ITERS,
                data_description=data_object["Description"],
                source=data_object["Source"],
                purpose=data_object["Purpose"],
                query=data_object["Query"]
            )

            def update_data_object_from_json(data_object, json_output):
                for field in ["Description", "Source", "Purpose", "Query", "Citation", "Data"]:
                    data_object[field] = json_output[field]

            msg_history = []
            text, msg_history = get_response_from_llm(
                gather_data_prompt.format(
                    current_iter=1,
                    num_iters=MAX_ITERS,
                    last_query_results=papers_str
                ),
                client=client,
                model=client_model,
                system_message=gather_data_system_prompt,
                msg_history=msg_history
            )
            
            json_output = extract_json_between_markers(text)
            assert json_output is not None, "Failed to extract JSON from LLM output"
            print(json_output)

            # iteratively improve
            for i in range(1, MAX_ITERS):
                update_data_object_from_json(data_object, json_output)
                
                if json_output["Data"] != "" and json_output["Citation"] != "":
                    break

                papers_str = get_papers(data_object["Query"])
                text, msg_history = get_response_from_llm(
                    gather_data_prompt.format(
                        current_iter=i+1,
                        num_iters=MAX_ITERS,
                        last_query_results=papers_str
                    ),
                    client=client,
                    model=client_model,
                    system_message=gather_data_system_prompt,
                    msg_history=msg_history
                )
                
                json_output = extract_json_between_markers(text)
                assert json_output is not None, "Failed to extract JSON from LLM output"
                print(json_output)
            
            update_data_object_from_json(data_object, json_output)
            if json_output["Data"] == "" and json_output["Citation"] == "":
                print(f"Warning: Data object {data_object['Description']} not found.")
            else:
                print(f"Data object {data_object['Description']} found.")
        except Exception as e:
            print(f"Error occurred when gathering data: {e}")
            return False
    
    # write the updated investigation.json file
    with open(osp.join(folder_name, "investigation.json"), "w") as f:
        json.dump(investigation, f, indent=4)

    return True

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
    start_investigation_prompt = coder_prompt.format(
        title=idea["Title"],
        idea=idea["Description"],
        max_queries=MAX_QUERIES
    )

    # 1. What data objects to gather
    coder_out = coder.run(start_investigation_prompt)
    print(coder_out)

    # 2. Gather the data that is possible to gather
    success = gather_data(idea, folder_name, client, client_model)
    if not success:
        print("Failed to gather all data objects.")
        return False
    
    # anything else before writeup?

    return True

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
