# Redis Bike Company Demo Application: Data Loader Script

from dotenv import load_dotenv
from redis.commands.search.indexDefinition import IndexDefinition, IndexType
from redis.commands.search.field import TextField, TagField, NumericField, GeoField
from redis.commands.search.query import Query
from redis.commands.search.aggregation import AggregateRequest
from redis.commands.search.reducers import count, count_distinct

import json
import io
import os
import redis

# Load environment variables / secrets from .env file.
load_dotenv()

BIKES_DATASET_SIZE = 0 # Number of bikes we expect to load.
STORES_DATASET_SIZE = 0 # Number of stores we expect to load.
REDIS_KEY_BASE = os.getenv("REDIS_KEY_BASE")
BIKE_KEY_BASE = os.getenv("BIKE_KEY_BASE")
STORE_KEY_BASE = os.getenv("STORE_KEY_BASE")
BIKE_INDEX_NAME = os.getenv("BIKE_INDEX_NAME")
STORE_INDEX_NAME = os.getenv("STORE_INDEX_NAME")
EXIT_CODE_ERROR = 1

# Q : what is difference between bike-key and bike-index name?
# A : bike-key is the prefix for the key of each bike document in Redis, and bike index name is the name of the search index that we will create to index the bike documents.

# Q : what is index definition for redis search?
# A : The index definition is a set of options that define how the search index will be created. It includes the index type (JSON in this case), the prefix to use for the keys of the documents to be indexed, and other options such as the language to use for stemming and stop words.
# Q : is it mean schema for search index?
# A : Yes, the index definition is a schema for the search index.

# Connect to Redis and reset to a known state.
print(f"Connecting to Redis.")
redis_client = redis.from_url(os.getenv("REDIS_URL"))

print(f"Deleting any existing data with {REDIS_KEY_BASE} prefix.")
pipeline = redis_client.pipeline(transaction = False)
for k in redis_client.scan_iter(match = f"{REDIS_KEY_BASE}:*"):
    pipeline.delete(k)

pipeline.execute()

print("Dropping any existing search indices.")

try:
    redis_client.ft(BIKE_INDEX_NAME).dropindex(delete_documents = False)
except:
    # Dropping an index that doesn't exist throws an exception 
    # but isn't an error in this case - we just want to start
    # from a known point.
    pass

try:
    redis_client.ft(STORE_INDEX_NAME).dropindex(delete_documents = False)
except:
    pass

print("Creating search index for bikes.")
redis_client.ft(BIKE_INDEX_NAME).create_index(
    [
        TagField("$.stockcode", as_name = "stockcode", sortable = True),
        TagField("$.model", as_name = "model", sortable = True),
        TagField("$.brand", as_name = "brand", sortable = True),
        TagField("$.type", as_name = "type", sortable = True),
        TextField("$.description", as_name = "description"),
        TagField("$.specs.material", as_name = "material", sortable = True),
        NumericField("$.specs.weight", as_name = "weight", sortable = True),
        NumericField("$.price", as_name = "price", sortable = True)
    ],
    definition = IndexDefinition(
        index_type = IndexType.JSON,
        prefix = [ f"{BIKE_KEY_BASE}:" ]
    )
)

print("Creating store search index.")
redis_client.ft(STORE_INDEX_NAME).create_index(
    [
        TagField("$.storecode", as_name = "storecode"),
        TagField("$.storename", as_name = "storename"),
        TagField("$.address.city", as_name = "city"), 
        TagField("$.address.state", as_name = "state"),
        TagField("$.address.pin", as_name = "pin"),
        TagField("$.address.country", as_name = "country"),
        GeoField("$.position", as_name = "position"),
        TagField("$.amenities", as_name = "amenities")
    ],
    definition = IndexDefinition(
        index_type = IndexType.JSON,
        prefix = [ f"{STORE_KEY_BASE}:" ]
    )
)

print(f"Loading bike data.")
bikes_loaded = 0

try:
    bikes_file = io.open("data/bike_data.json", encoding = "utf-8")
    all_bikes = json.load(bikes_file)
    bikes_file.close()

    # Use a pipeline to load all the bike documents into Redis.
    pipeline = redis_client.pipeline(transaction = False)

    for bike in all_bikes["data"]:
        bike_key = f"{BIKE_KEY_BASE}:{bike['stockcode'].lower()}"
        pipeline.json().set(bike_key, "$", bike)
        bikes_loaded += 1
        print(f"{bike_key} - {bike['brand']} {bike['model']}")

    pipeline.execute()
except Exception as e:
    print("Failed to load bikes file:")
    print(e)
    os._exit(EXIT_CODE_ERROR)

print(f"Loaded {bikes_loaded} bikes into Redis.")

print(f"Loading store data.")
stores_loaded = 0

try:
    stores_file = io.open("data/store_data.json", encoding = "utf-8")
    all_stores = json.load(stores_file)
    bikes_file.close()

    # Use a pipeline to load all the store documents into Redis.
    pipeline = redis_client.pipeline(transaction = False)

    for store in all_stores["data"]:
        store_key = f"{STORE_KEY_BASE}:{store['storecode'].lower()}"
        pipeline.json().set(store_key, "$", store)
        stores_loaded += 1
        print(f"{store_key} - {store['storename']}")

    pipeline.execute()
except Exception as e:
    print("Failed to load stores file:")
    print(e)
    os._exit(EXIT_CODE_ERROR)

print(f"Loaded {stores_loaded} stores into Redis.")

print("Verifying data...")

try:
    # Check that the "Eva Europa" is bike RBC00100.
    # ft.search idx:bikes "@brand:{Eva} @model:{Europa}" return 1 stockcode
    results = redis_client.ft(BIKE_INDEX_NAME).search(Query("@brand:{Eva} @model:{Europa}").return_field("stockcode"))
    assert 1 == len(results.docs), "Error searching for Eva Europa RBC00100."
    assert "RBC00100" == results.docs[0].stockcode, "Incorrect stockcode returned for Eva Europa."

    # Check that there are 7 bikes in the INR 150000-159999 price range.
    # ft.aggregate idx:bikes "@price:[150000 159999]" groupby 0 reduce count 0 as numbikes
    result = redis_client.ft(BIKE_INDEX_NAME).aggregate(AggregateRequest("@price:[150000 159999]").group_by([], count().alias("numbikes")))
    assert 1 == len(result.rows), "Error counting bikes in 150000-159999 price range."
    assert 7 == int(result.rows[0][1]), "Wrong number of bikes in 150000-159999 price range."

    # Check that there are 7 different types of bike.
    # ft.aggregate idx:bikes "*" groupby 0 reduce count_distinct 1 type as numtypes
    result = redis_client.ft(BIKE_INDEX_NAME).aggregate(AggregateRequest("*").group_by([], count_distinct("type").alias("numtypes")))
    assert 1 == len(result.rows), "Error counting distinct types of bike."
    assert 7 == int(result.rows[0][1]), "Wrong number of distinct types of bike."

    # Check that store with pin "400098" is in Mumbai.
    # ft.search idx:stores "@pin:{400098}" return 1 city
    results = redis_client.ft(STORE_INDEX_NAME).search(Query("@pin:{400098}").return_field("city"))
    assert 1 == len(results.docs), "Error searching for Mumbai store."
    assert "Mumbai" == results.docs[0].city, "Incorrect city returned for the 400098 store."

    # Check that there are 5 stores in India.
    # ft.aggregate idx:stores "@country:{India}" groupby 0 reduce count 0 as indianstores
    result = redis_client.ft(STORE_INDEX_NAME).aggregate(AggregateRequest("@country:{India}").group_by([], count().alias("indianstores")))
    assert 1 == len(result.rows), "Error counting stores in India."
    assert 5 == int(result.rows[0][1]), "Wrong number of stores found in India."

    # Check that 2 stores have parking and offer rentals.
    # ft.aggregate idx:stores "@amenities:{parking} @amenities:{rentals}" groupby 0 reduce count 0 as parkingandrentals
    result = redis_client.ft(STORE_INDEX_NAME).aggregate(AggregateRequest("@amenities:{parking} @amenities:{rentals}").group_by([], count().alias("parkingandrentals")))
    assert 1 == len(result.rows), "Error counting stores with parking and rentals."
    assert 2 == int(result.rows[0][1]), "Wrong number of stores found with parking and rentals."

    # Check that we get the Kanpur store back when performing a search from 
    # a position in Lucknow with a 100km radius.
    # ft.search idx:stores "@position:[80.8599399 26.848668 100 km]" return 1 storecode
    results = redis_client.ft(STORE_INDEX_NAME).search(Query("@position:[80.8599399 26.848668 100 km]").return_field("storecode"))
    assert 1 == len(results.docs), "Error in geo search results."
    assert "KA" == results.docs[0].storecode, "Incorrect store code returned for store geo search."

except AssertionError as e:
    # Something went wrong :(
    print("Data verification checks failed:")
    print(e)
    redis_client.quit()
    os._exit(EXIT_CODE_ERROR)

# All done!    
print("Data verification checks completed OK.")
redis_client.quit()