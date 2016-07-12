import sys, os, math, re, unicodedata, click
from timeit import default_timer as timer
from nltk.tag import pos_tag 
from nltk import word_tokenize
from nltk.stem.porter import *
from nltk.stem import RSLPStemmer
from nltk.corpus import stopwords
from pymongo import MongoClient
from bson.objectid import ObjectId
from pyspark import SparkConf, SparkContext
from Classifier import Classifier
from twython import Twython
import facebook
from langdetect import detect
#from base import *
# encoding=utf8  

reload(sys)  
sys.setdefaultencoding('utf8')

start = timer()

APP_NAME = 'Recomender System'
threshold  = 0.1
numMaxSuggestionsPerPost = 5
numStarts = 5

############ INICIO BASE
host = 'localhost'
port = 27017
username = ''
password = ''
database = 'recsysdb'

def removeAccents(s):
  s = ''.join((c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn'))
  return re.sub(r'[^\w]', ' ', s)
    
def createMongoDBConnection(host, port, username, password, db):
	client = MongoClient(host, port)
	return client[db]

def findUserById(userId):
    db = createMongoDBConnection(host, port, username, password, database)
    return db.usuario.find_one({'_id': ObjectId(userId)})

def findProductById(prodId):
    db = createMongoDBConnection(host, port, username, password, database)
    return db.produto.find_one({'_id': ObjectId(prodId)})

def findProductsByCategory(categories):
    db = createMongoDBConnection(host, port, username, password, database)
    produtos = db.produto
    product_list = []
    query_filter = {}
    if categories:
      query_filter = {"categorias" : {"$in" : categories}}
    
    print '#### Find products by query {}'.format(query_filter)
    for produto in produtos.find(query_filter):
      keys = produto.keys()
      description = ''
      if 'descricaoLonga' in keys:
          description = removeAccents(description + produto['descricaoLonga'])
      if 'nome' in keys:
          description = removeAccents(description + produto ['nome'])
      id = None
      if '_id' in keys:
          id = str(produto['_id'])
      
      category = ''
      subcategory = ''
      if 'categorias' in keys:
          category = removeAccents(produto['categorias'][0])
          if(len(produto['categorias']) > 1):
              subcategory = removeAccents(produto['categorias'][1])
          
      product_list.append((id, description, category, subcategory))
    
    return product_list

def insertTokensAndCategories(tokens, category, categoryAndSubcategory):
    db = createMongoDBConnection(host, port, username, password, database)

    modelCollection = db.model
    modelCollection.remove({'_type':'token'})

    document_mongo =  dict()
    document_mongo['_type'] = 'token'
    document_mongo['_datetime'] = datetime.datetime.utcnow()
    i = 0
    for t in tokens:
        document_mongo[t] = i
        i = i + 1   

    modelCollection.insert_one(document_mongo)

    modelCollection.remove({'_type':'category'})

    document_mongo =  dict()
    document_mongo['_type'] = 'category'
    document_mongo['_datetime'] = datetime.datetime.utcnow()
    i = 0
    for c in category:
        document_mongo[c] = i
        i = i + 1 

    modelCollection.insert_one(document_mongo)

    modelCollection.remove({'_type':'category and subcategory'})
    
    document_mongo =  dict()
    document_mongo['_type'] = 'category and subcategory'
    document_mongo['_datetime'] = datetime.datetime.utcnow()
    i = 0
    for c in categoryAndSubcategory:
        document_mongo[c[0]+","+c[1]] = i
        i = i + 1 

    modelCollection.insert_one(document_mongo)
    

def updateUser(user):
    db = createMongoDBConnection(host, port, username, password, database)
    return db.usuario.save(user)

def getTokensAndCategories():  
    db = createMongoDBConnection(host, port, username, password, database)
    model = db.model
    
    tokens_dict = db.model.find({"_type": "token"}).limit(1).next()
    del tokens_dict['_type']
    del tokens_dict['_id']
    del tokens_dict['_datetime']
    tokens_list = [None] * (max(tokens_dict.values()) + 1)

    for key, value in tokens_dict.iteritems():
        tokens_list[value] = key

    categories_dict = db.model.find({"_type": "category"}).limit(1).next()
    del categories_dict['_type']
    del categories_dict['_id']
    del categories_dict['_datetime']
    categories_list = [None] * (max(categories_dict.values()) + 1)

    for key, value in categories_dict.iteritems():
        categories_list[value] = key

    categories_and_subcategories_dict = db.model.find({"_type": "category and subcategory"}).limit(1).next()
    del categories_and_subcategories_dict['_type']
    del categories_and_subcategories_dict['_id']
    del categories_and_subcategories_dict['_datetime']
    categories_and_subcategories_list = [None] * (max(categories_and_subcategories_dict.values()) + 1)

    for key, value in categories_and_subcategories_dict.iteritems():
        pre_string = key.split(",")
        categories_and_subcategories_list[value] = (pre_string[0], pre_string[1])

    return tokens_list, categories_list, categories_and_subcategories_list
    
def tf(tokens):
    """ Compute TF
    Args:
        tokens (list of str): input list of tokens from tokenize
    Returns:
        dictionary: a dictionary of tokens to its TF values
    """
    token_dict = dict()   
    for token in tokens:
        if token in token_dict:
            token_dict[token] = token_dict[token] + 1
        else:
            token_dict[token] = 1
            
    for t in token_dict:
        token_dict[t] = float(token_dict[t])/float(len(tokens))
        
    return token_dict

def idfs(corpus):
    """ Compute IDF
    Args:
        corpus (RDD): input corpus
    Returns:
        RDD: a RDD of (token, IDF value)
    """
    N = corpus.count()
    uniqueTokens = corpus.flatMap(lambda doc: set(doc[1]))
    tokenCountPairTuple = uniqueTokens.map(lambda t: (t, 1))
    tokenSumPairTuple = tokenCountPairTuple.reduceByKey(lambda a,b: a+b)

    return tokenSumPairTuple.map(lambda (k, v): (k, math.log(1.0*N/v)))    


def tfidf(tokens, idfs):
    """ Compute TF-IDF
    Args:
        tokens (list of str): input list of tokens from tokenize
        idfs (dictionary): record to IDF value
    Returns:
        dictionary: a dictionary of records to TF-IDF values
    """
    tfs = tf(tokens)
    tfIdfDict = {k: v*idfs[k] for k, v in tfs.items()}
    return tfIdfDict

def dotprod(a, b):
    """ Compute dot product
    Args:
        a (dictionary): first dictionary of record to value
        b (dictionary): second dictionary of record to value
    Returns:
        dotProd: result of the dot product with the two input dictionaries
    """
    dp=0
    for k in a:
        if k in b:
            dp += a[k] * b[k]
    return  dp


def norm(a):
    """ Compute square root of the dot product
    Args:
        a (dictionary): a dictionary of record to value
    Returns:
        norm: a dictionary of tokens to its TF values
    """
    return math.sqrt(dotprod(a, a))

############ FIM BASE

def cosineSimilarity(record, idfsRDD, idfsRDD2, corpusNorms1, corpusNorms2):
    """ Compute Cosine Similarity using Broadcast variables
    Args:
        record: ((ID1, ID2), token), RDDs (Broadcast) with idfs and norm values
    Returns:
        pair: ((ID1, ID2), cosine similarity value)
    """
    vect1Rec = record[0][0]
    vect2Rec = record[0][1]
    key = (vect1Rec, vect2Rec)

    try:
        tokens = record[1]
        s = sum((idfsRDD[vect1Rec][i]*idfsRDD2[vect2Rec][i] for i in tokens))
        value = s/((corpusNorms1[vect1Rec])*(corpusNorms2[vect2Rec]))
        return (key, value)
    except:
        print "Unexpected error:", sys.exc_info()[0]
    return (key, 0)

def findPosts(user):
    posts = []
    # TWITTER
    if('twitterId' in user):
        CONSUMER_KEY = 'p05WZVs4JivX4a0WSwFyMXXCo'
        CONSUMER_SECRET = 'DghsY9Dxn2X8xAjdQKEvwBLtqsHNJabFz361pz2ZvRmAgXiPHB'
        ACCESS_KEY = '167813147-LwEOQAqO6RCnK0GfIEXNeVOng93QHkW1iFuVjBUV'
        ACCESS_SECRET = 'kpzp3quxTmVpSfWdgcyN5qbrPTmyoFArdvJeUC4Dfjtg1'

        twitter = Twython(CONSUMER_KEY,CONSUMER_SECRET,ACCESS_KEY,ACCESS_SECRET)
        user_timeline = twitter.get_user_timeline(screen_name=user['twitterId'], count=100, include_retweets=False)
    

        for tweet in user_timeline:
            tweet_utf = removeAccents(unicode(tweet['text'].encode('utf-8')))
            if(detect(tweet_utf) == 'pt'):
                tweet_id = unicode(str(tweet['id']))
                if(tweet_id and tweet_utf):
                    posts.append((tweet_id, tweet_utf, u'Post', u'Twitter'))            
    # FACEBOOK                
    if('facebookId' in user):
        app_id = "704203256284579"
        app_secret = "9a75ef350e4f9b24d8be454abf29ae68"
        access_token = facebook.GraphAPI().get_app_access_token(app_id, app_secret)
        graph = facebook.GraphAPI(access_token)
        profile = graph.get_object(user['facebookId'])
        f_posts = graph.get_connections(profile['id'], 'posts')

        for f_post in f_posts['data']:
            if 'message' in f_post and len(f_post['message']) > 10:
                posts.append((f_post['id'], f_post['message'], u'Post', u'Facebook'))
            if 'description' in f_post and len(f_post['description']) > 10:
                posts.append((f_post['id'], f_post['description'], u'Post', u'Facebook'))

    return posts

def main(**kwargs):
    iduser = sys.argv[1]

    #Find user by id
    user = findUserById(iduser)
    
    #Find tweets and posts (here or on java?)
    posts = findPosts(user)
    
    #posts = [(u'1', u'Eu amo computadores! Eu gostaria de comprar um asus notebook.', u'Post', u'Twitter'),(u'2', u'Minha geladeira quebrou!...', u'Post', u'Facebook'),(u'3', u'Eu amo assistir TV todo sabado anoite!', u'Post', u'Twitter'),(u'4', u'Eu amo assitir netflix na minha Smart TV', u'Post', u'Twitter')]

    conf = SparkConf().setAppName(APP_NAME).setMaster("local").set("spark.executor.memory", "1g")

    sc = SparkContext(conf=conf)

    for post in posts:
        print post

    print 'Generating posts RDD'
    postsRDD = sc.parallelize(posts)
    tokens, category, categoryAndSubcategory = getTokensAndCategories()
    stpwrds = stopwords.words('portuguese')

    print 'Generating product RDD'
    productRDD = sc.parallelize(findProductsByCategory([]))

    print 'Union posts with product'
    productAndPostRDD = productRDD.union(postsRDD)
    
    print 'Generating corpusRDD'
    corpusRDD = (productAndPostRDD.map(lambda s: (s[0], word_tokenize(s[1].lower()), s[2], s[3]))
                           .map(lambda s: (s[0], [PorterStemmer().stem(x) for x in s[1] if x not in stpwrds], s[2], s[3]))
                           .map(lambda s: (s[0], [x for x in s[1] if x in tokens], s[2], s[3]))
                           .filter(lambda x: len(x[1]) >= 20 or x[2] == u'Post')
                           .cache())

    #corpusRDD = productAndPostRDD.map(lambda s: (s[0], word_tokenize(s[1].lower()), s[2], s[3])).map(lambda s: (s[0], [PorterStemmer().stem(x) for x in s[1] if x not in stpwrds], s[2], s[3] )).map(lambda s: (s[0], [x[0] for x in pos_tag(s[1]) if x[1] == 'NN' or x[1] == 'NNP'], s[2], s[3])).cache()
    print 'Generating idfsRDD'
    idfsRDD = idfs(corpusRDD)
    idfsRDDBroadcast = sc.broadcast(idfsRDD.collectAsMap())
    print 'Generating tdidfRDD'
    tfidfRDD = corpusRDD.map(lambda x: (x[0], tfidf(x[1], idfsRDDBroadcast.value), x[2], x[3])).cache()
    
    tfidfPostsRDD = tfidfRDD.filter(lambda x: x[2]=='Post').cache()
    tfidfPostsBroadcast = sc.broadcast(tfidfPostsRDD.map(lambda x: (x[0], x[1])).collectAsMap())
    corpusPostsNormsRDD = tfidfPostsRDD.map(lambda x: (x[0], norm(x[1]))).cache()
    corpusPostsNormsBroadcast = sc.broadcast(corpusPostsNormsRDD.collectAsMap())
    
    print 'Generating Classifier'
    #classifier = Classifier(sc, 'NaiveBayes')
    #modelNaiveBayesCategory = classifier.getModel('/dados/models/naivebayes/category_new')
    #postsSpaceVectorRDD = classifier.createVectSpacePost(tfidfPostsRDD, tokens)
    #predictions = postsSpaceVectorRDD.map(lambda p: (modelNaiveBayesCategory.predict(p[1]), p[0])).groupByKey().mapValues(list).collect()
    
    classifier = Classifier(sc, 'NaiveBayes')
    modelNaiveBayesSubcategory = classifier.getModel('/dados/models/naivebayes/subcategory_new')
    postsSpaceVectorRDD = classifier.createVectSpacePost(tfidfPostsRDD, tokens)
    predictions = postsSpaceVectorRDD.map(lambda p: (modelNaiveBayesSubcategory.predict(p[1]), p[0])).groupByKey().mapValues(list).collect()
    
    #classifier = Classifier(sc, 'DecisionTree')
    #modelDecisionTree = classifier.getModel('/dados/models/dt/category_new')
    #postsSpaceVectorRDD = classifier.createVectSpacePost(tfidfPostsRDD, tokens)
    #predictions = modelDecisionTree.predict(postsSpaceVectorRDD.map(lambda x: x)).collect()
    
    for prediction in predictions:
        print '=================================> PREDICTION {}'.format(prediction)
        category_to_use = categoryAndSubcategory[int(prediction[0])][0]
        print '=================================> CATEGORY TO USE {}'.format(category_to_use)
        
        tfidfProductsCategoryRDD = tfidfRDD.filter(lambda x: x[2]==category_to_use).cache()
        tfidfProductsCategoryBroadcast = sc.broadcast(tfidfProductsCategoryRDD.map(lambda x: (x[0], x[1])).collectAsMap())

        corpusInvPairsProductsRDD = tfidfProductsCategoryRDD.flatMap(lambda r: ([(x, r[0]) for x in r[1]])).cache()
        corpusInvPairsPostsRDD = tfidfPostsRDD.flatMap(lambda r: ([(x, r[0]) for x in r[1]])).filter(lambda x: x[1] in prediction[1]).cache()
        commonTokens = (corpusInvPairsProductsRDD.join(corpusInvPairsPostsRDD)
                                                 .map(lambda x: (x[1], x[0]))
                                                 .groupByKey()
                                                 .cache())

        corpusProductsNormsRDD = tfidfProductsCategoryRDD.map(lambda x: (x[0], norm(x[1]))).cache()
        corpusProductsNormsBroadcast = sc.broadcast(corpusProductsNormsRDD.collectAsMap())

        print '### PREDICTION Similarities RDD'
        similaritiesRDD =  (commonTokens
                            .map(lambda x: cosineSimilarity(x, tfidfProductsCategoryBroadcast.value, tfidfPostsBroadcast.value, corpusProductsNormsBroadcast.value, corpusPostsNormsBroadcast.value))
                            .cache())

        suggestions = (similaritiesRDD
                        .map(lambda x: (x[0][1], (x[0][0], x[1])))
                        .filter(lambda x: x[1][1]>threshold)
                        .groupByKey()
                        .mapValues(list)
                        .join(postsRDD)
                        .join(postsRDD.map(lambda x: (x[0], x[3])))
                        .collect())

        if len(suggestions) > 0:
            insertSuggestions(suggestions, iduser, productRDD)
        
    user['statusRecomendacao'] = u'F'
    updateUser(user)
    
    elap = timer()-start
    print 'it tooks %d seconds' % elap


def insertSuggestions(suggestions_list, iduser, productRDD):
    suggestions_to_insert = []
    for post in suggestions_list:
        if len(post) > 0:
            suggestions_dict = dict()
            product_dict = dict()
            suggestions_dict['userId'] = iduser
            suggestions_dict['postId'] = post[0]
            suggestions_dict['post'] = post[1][0][1]
            suggestions_dict['resource'] = post[1][1]
            post[1][0][0].sort(key=lambda x: -x[1])
            if len(post[1][0][0]) > 0:
                suggestions_dict['suggestions'] = []
                maxCosine = max([x[1] for x in post[1][0][0][:numMaxSuggestionsPerPost]])
                minCosine = 0
                lenInterval = (maxCosine - minCosine)/numStarts
                suggestions_dict['products'] = []
                for product in post[1][0][0][:numMaxSuggestionsPerPost]:
                    print '###### PRODUCT RECOMMENDATION {} '.format(product)
                    product_dict = dict()
                    prod = findProductById(product[0])
                    if prod:
                        prod['cosineSimilarity'] = product[1]
                        prod['rate'] = (product[1]-minCosine)/lenInterval
                        suggestions_dict['products'].append(prod)
            suggestions_to_insert.append(suggestions_dict)
    db = createMongoDBConnection(host, port, username, password, database)
    db.recomendacao.insert_many(suggestions_to_insert)
    return True

if __name__ == '__main__':
    main()
    
