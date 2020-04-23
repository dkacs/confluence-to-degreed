from utils import *
import logging
import json
import hashlib
import sys
import tempfile
import csv
import pysftp
from collections import OrderedDict
from requests.auth import HTTPBasicAuth

# Setting up some pseudo constants
LOG_LEVEL = logging.INFO if ("DEBUG" not in os.environ) else logging.DEBUG

#logging.basicConfig(format='%(asctime)s - %(message)s', level=LOG_LEVEL)
root = logging.getLogger()
root.setLevel(LOG_LEVEL)

handler = logging.StreamHandler(sys.stdout)
handler.setLevel(LOG_LEVEL)
formatter = logging.Formatter('%(asctime)s - %(message)s')
handler.setFormatter(formatter)
root.addHandler(handler)

# processRow - simplifies the process of processing athena rows
#              it takes a row argument - a single row from the response,
#              actually the "Data" element, and then a list of fieldnames.
def processRow(row, fieldnames):
  row_dict = OrderedDict()

  logging.info("Row: %s" % row)

  for itm in range(0, len(row)):
    if 'VarCharValue' in row[itm]:
      row_dict[fieldnames[itm]] = row[itm]['VarCharValue']
    else:
      row_dict[fieldnames[itm]] = ""
  logging.info("ROW DICT: %s", json.dumps(row_dict))
  return row_dict

# The handler will run two SQLs against Athena:
#   1. pathway_sql - the sql that queries for wiki content that will be removed from the 
#      catalog, and the pathway(s) that contain that content, to allow email_notify to 
#      notify them of the content disappearing
#   2. to_upsert_sql - the sql that will generate the content update file (inclusive of 
#      content to include as well as to remove)
def lambda_handler(event, context):
  config = getParamInfo()
  template_values(config,config,tokens)
  
  pathway_sql = 'select "learningtools"."adsuserinfo".email as send_to, "confluencetodegreed".degreed_articles.url as wiki_url, "learningtools"."degreed_published_pathway_summary"."title" as "pathway_title" from "confluencetodegreed".degreed_articles left join wiki_spaces on "confluencetodegreed".degreed_articles.url=wiki_spaces.url left join "learningtools"."degreed_published_pathway_details" on "learningtools"."degreed_published_pathway_details"."content_id"="confluencetodegreed".degreed_articles.contentid left join "learningtools"."degreed_published_pathway_summary" on  "learningtools"."degreed_published_pathway_summary"."pathway_id"="learningtools"."degreed_published_pathway_details"."pathway_id" left join "learningtools"."adsuserinfo" on  "learningtools"."degreed_published_pathway_summary"."created_by"=concat("learningtools"."adsuserinfo"."first_name", \' \', "learningtools"."adsuserinfo"."last_name") where wiki_spaces.contentid is null and email is not null'
  to_upsert_sql = """
  select * from (select \"confluencetodegreed\".wiki_spaces.contenttype as ContentType,
                \"confluencetodegreed\".wiki_spaces.contentid as ContentId, 
                \"confluencetodegreed\".wiki_spaces.url as URL, 'N' as \"Delete\", 
                \"confluencetodegreed\".wiki_spaces.title as Title, '' as Summary, 
                'https://dpe-support.autodesk.com/images/adsk-wiki-logo.png' as ImageURL, '' as Duration, '' as Language, 'autodeskwiki' as Provider, 
                '' as CEU, '' as Format, '' as DurationUnits, '' as \"Publish Date\", 
                \"confluencetodegreed\".wiki_spaces.owners as Owners, 
                \"confluencetodegreed\".wiki_spaces.topic1 as Topic1, 
                \"confluencetodegreed\".wiki_spaces.topic2 as Topic2, 
                \"confluencetodegreed\".wiki_spaces.topic3 as Topic3, 
                \"confluencetodegreed\".wiki_spaces.topic4 as Topic4, 
                \"confluencetodegreed\".wiki_spaces.topic5 as Topic5, 
                \"confluencetodegreed\".wiki_spaces.topic6 as Topic6, 
                \"confluencetodegreed\".wiki_spaces.topic7 as Topic7, 
                \"confluencetodegreed\".wiki_spaces.topic8 as Topic8, 
                \"confluencetodegreed\".wiki_spaces.topic9 as Topic9, 
                \"confluencetodegreed\".wiki_spaces.topic10 as Topic10, 
                \"confluencetodegreed\".wiki_spaces.topic11 as Topic11, 
                \"confluencetodegreed\".wiki_spaces.topic12 as Topic12, 
                \"confluencetodegreed\".wiki_spaces.topic13 as Topic13, 
                \"confluencetodegreed\".wiki_spaces.topic14 as Topic14, 
                \"confluencetodegreed\".wiki_spaces.topic15 as Topic15, 
                \"confluencetodegreed\".wiki_spaces.topic16 as Topic16, 
                \"confluencetodegreed\".wiki_spaces.topic17 as Topic17, 
                \"confluencetodegreed\".wiki_spaces.topic18 as Topic18, 
                \"confluencetodegreed\".wiki_spaces.topic19 as Topic19, 
                \"confluencetodegreed\".wiki_spaces.topic20 as Topic20 
                from \"confluencetodegreed\".wiki_spaces 
          union all select 'Article' as ContentType, 
                \"confluencetodegreed\".degreed_articles.contentid as ContentId, 
                \"confluencetodegreed\".degreed_articles.url as URL, 'Y' as \"Delete\", 
                \"confluencetodegreed\".degreed_articles.title as Title,
                '' as Summary, '' as ImageURL, '' as Duration, '' as Language,
                '' as Provider, '' as CEU, '' as Format, '' as DurationUnits,
                '' as \"Publish Date\", '' as Owners, '' as Topic1, '' as Topic2,
                '' as Topic3, '' as Topic4, '' as Topic5, '' as Topic6, '' as Topic7,
                '' as Topic8, '' as Topic9, '' as Topic10, '' as Topic11, '' as Topic12,
                '' as Topic13, '' as Topic14, '' as Topic15, '' as Topic16, '' as Topic17,
                '' as Topic18, '' as Topic19, '' as Topic20                
                from \"confluencetodegreed\".degreed_articles 
              left join \"confluencetodegreed\".wiki_spaces 
              on \"confluencetodegreed\".degreed_articles.url=\"confluencetodegreed\".wiki_spaces.url 
              where \"confluencetodegreed\".wiki_spaces.contentid is null)
              """
  
  # Set the Athena DB prefers it as a parameter, but will default...
  if "athena_db" not in config:
    config['athena_db'] = "confluencetodegreed"

  # Run the upsert SQL
  results = athena_query(config, to_upsert_sql)
  logging.debug("Results: %s" % results)
  logging.info("Result Count: %d" % len(results))
  if (len(results) < 2):
    logging.info("No Data Found - No File being Generated")
  else:
    # If we have data in the response, generate the CSV file.
    tblhdrs_meta = results.pop(0)['Data']
    rows = results


    logging.debug("Return from query: %s" % json.dumps(rows))
    if (len(rows) <= 1):
      logging.info("No Data Found - No File being Generated")
    else:
      # Get the fieldnames from the header of the data, and normalize the data
      # (i.e. take out the athena generated "structure" - the "VarCharValue" thing)
      fieldnames = []
      for hdr in tblhdrs_meta:
        fieldnames.append(hdr['VarCharValue'])
      if "dry_run" in config:
        filename = "STEP_FUNCTION_TESTING_File_%s.csv" % date.today().isoformat().replace('-','')
      else:
        filename = "Content_File_%s.csv" % date.today().isoformat().replace('-','')

      # generate the CSV file
      with open("/tmp/%s" % filename, 'w') as fp:
        logging.debug("Fieldnames: %s" % fieldnames)
        writer = csv.DictWriter(fp, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        writer.writeheader()

        # Process each row appropriately to create the CSV
        for rowcont in rows:
          row = rowcont['Data']
          row_dict = processRow(row, fieldnames)
          writer.writerow(row_dict)
      
      # If we have an sftp config, use it to upload the content
      # to the degreed ftp site.
      if "degreed" in config and "sftp" in config['degreed']:
        sftpconf = config['degreed']['sftp']
        cnopts = pysftp.CnOpts()
        cnopts.hostkeys = None
        os.chdir("/tmp")
        with pysftp.Connection(sftpconf['host'], username=sftpconf['username'], password=sftpconf['password'],
                               cnopts=cnopts) as sftp:
          with sftp.cd('inbound'):
            sftp.put(filename)

  # Run the pathway sql to generate the email list, and then put it
  # as the return value so it gets passed to email_notify.py
  results = athena_query(config, pathway_sql)
  
  logging.debug("Results: %s" % results)
  logging.info("Result Count: %d" % len(results))
  if (len(results) < 2):
    logging.info("No Data Found - No emails to Send")
  else:
    # Get the fieldnames from the header of the data, and normalize the data
    # (i.e. take out the athena generated "structure" - the "VarCharValue" thing)
    obj_meta = results.pop(0)['Data']
    rows = results

    logging.debug("Return from query: %s" % json.dumps(rows))
    if (len(rows) <= 1):
      logging.info("No Data Found - No emails to send")
    else:
      fieldnames = []
      resout = []
      for hdr in obj_meta:
        fieldnames.append(hdr['VarCharValue'])
        
      # Process each row appropriately to create output structure
      for rowcont in rows:
        row = rowcont['Data']
        row_dict = processRow(row, fieldnames)
        resout.append(row_dict)
      
      return(resout)

