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
MAX_QUERIES = 10 # max num of different data objects to gather
MAX_STDERR_OUTPUT = 1500

coder_prompt = """Your goal is to investigate the following idea: {title}.
The proposed investigation is as follows: {idea}.
You are given a total of up to {max_queries} research queries to complete the investigation. You do not need to use all {max_queries}.

First, plan the list of data objects you would like to gather.
Modify and duplicate the example in `investigation.json` for each query.
Change the `"Description"` field to describe the contents of the `"Data"` object you would like to have.
Change the `"Exists"` and `"Source"` fields to indicate if the data should already exist (e.g., from a prior study or on the internet) or if it needs to be gathered from scratch as during this proposed research.
Change the `"Phase"` and `"Purpose"` fields to explain if it is needed for the proposal justification (e.g., data showing the scale or impact of the problem, examples of similar successful research in other contexts, other statistics), or if it is needed for the actual investigation (can also be existing data or new data collection that needs to be sourced by the researcher but can't be done in just this proposal).
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
        return "No papers found. Try to make the query broader or different altogether."

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
{{
    "Description": "{data_description}",
    "Source": "{source}",
    "Purpose": "{purpose}",
    "Query": "{query}",
    "Citation": "",
    "Data": {{}}
}}
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
- Be sure the "Data" field is a valid JSON format: either leave it in {{}} form with proper key-values, or use an array or simply a single string for the data.
Option 2: If you need to refine the query, fill in the "Query" field with a new query to search for papers and leave the other fields unchanged.
Option 3: If you must change the data object description because the original data cannot be found or you deem it unfit, you may change any of the fields except "Data" and "Citation".

A query will work best if you are able to recall the exact name of the paper you are looking for, or the authors.
This JSON will be automatically parsed, so ensure the format is precise, especially the "Data" field.'''

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
        if data_object["Phase"] == "Investigation":
            continue
        # must have a query (should be filled in by coder if exists)
        assert data_object["Query"] != "", "Query must be filled in for data object that exists and is needed for proposal."

        original_data_object = data_object.copy()
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

            # run rounds and iteratively improve
            cur_iter = 1
            while cur_iter <= MAX_ITERS:
                if data_object["Data"] is not {} and data_object["Citation"] != "":
                    break

                print(f"Round {cur_iter}/{MAX_ITERS}")

                # search query in Semantic Scholar, and let language model either generate the data object or refine the query
                papers_str = get_papers(data_object["Query"])
                text, msg_history = get_response_from_llm(
                    gather_data_prompt.format(
                        current_iter=cur_iter,
                        num_iters=MAX_ITERS,
                        last_query_results=papers_str
                    ),
                    client=client,
                    model=client_model,
                    system_message=gather_data_system_prompt,
                    msg_history=msg_history
                )

                # # drop old rounds from context to use less tokens, they shouldn't really be needed and this will help with token limits
                # if len(msg_history) >= 4:
                #     msg_history = msg_history[2:]
                # truncate papers_str in completed rounds, should not really be needed anymore and this will help with token limits
                msg_history[-2] = gather_data_prompt.format(
                    current_iter=cur_iter,
                    num_iters=MAX_ITERS,
                    last_query_results=(papers_str[:500] + "..." + papers_str[-500:]) if len(papers_str) > 1000 else papers_str
                )
                
                json_output = extract_json_between_markers(text)
                print(text)
                assert json_output is not None, "Failed to extract JSON from LLM output"

                cur_iter += 1

                if json_output["Description"] != data_object["Description"]:
                    if cur_iter == MAX_ITERS:
                        break # exit without updating so that the last data object is left as is
                    print("Warning: Data object description changed.")
                    # cur_iter -= 1 # allow one more iteration to try again

                update_data_object_from_json(data_object, json_output)
            
            if data_object["Data"] == {} and data_object["Citation"] == "":
                print(f"Warning: Data object \"{data_object['Description']}\" not found.")
                data_object["Data"] = "NOTE: Data was not found in the sources available." # note explicitly for writeup that this data was not found
            else:
                print(f"Data object \"{data_object['Description']}\" found after {cur_iter - 1} iterations.")
        except Exception as e:
            print(f"Error occurred when gathering data: {e}")
            return False
    
    # write the updated investigation.json file
    with open(osp.join(folder_name, "investigation.json"), "w") as f:
        json.dump(investigation, f, indent=4)

    return True

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
        print("Not all data objects were gathered.")
        return False
    
    # 3. have coder modify the notes.txt file
    next_prompt = """The data objects needed for the proposal phase have been gathered.
Please modify `notes.txt` with a description of the data objects gathered and how they support the investigation proposal. Include all citations.
If any data objects were not found, discuss how this may impact the proposal.
- Does this weaken the proposal?
- Can the proposal still be justified without this data? (e.g., by making assumptions with a disclaimer)
- Remember, transparency is key. The goal is to not to deceive but to be honest about the limitations of the proposal while still making a strong case for the investigation.
Also include any notes about the other data objects planned for the investigation phase, such as expanding on how they would be gathered, how they would be used, and the implications of various possible results.
Be sure to name the file and use the correct edit format.
"""
    coder_out = coder.run(next_prompt)
    print(coder_out)

    return True