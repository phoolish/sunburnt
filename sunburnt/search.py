from __future__ import absolute_import

import collections, copy, re

from .schema import SolrError, SolrUnicodeField, SolrBooleanField


class LuceneQuery(object):
    default_term_re = re.compile(r'^\w+$')
    range_query_templates = {
        "lt": "{* TO %s}",
        "lte": "[* TO %s]",
        "gt": "{%s TO *}",
        "gte": "[%s TO *]",
        "rangeexc": "{%s TO %s}",
        "range": "[%s TO %s]",
    }
    def __init__(self, schema, option_flag=None, original=None):
        self.schema = schema
        if original is None:
            self.option_flag = option_flag
            self.terms = collections.defaultdict(set)
            self.phrases = collections.defaultdict(set)
            self.ranges = set()
            self.subqueries = []
            self._or = self._and = self._not = self._pow = None
        else:
            self.option_flag = original.option_flag
            self.terms = copy.copy(original.terms)
            self.phrases = copy.copy(original.phrases)
            self.ranges = copy.copy(original.ranges)
            self.subqueries = [q.clone() for q in original.subqueries]
            self._or = original._or
            self._and = original._and
            self._not = original._not
            self._pow = original._pow

    def clone(self):
        return LuceneQuery(self.schema, original=self)

    @property
    def options(self):
        opts = {}
        s = unicode(self)
        if s:
            opts[self.option_flag] = s
        return opts

    # Below, we sort all our value_sets - this is for predictability when testing.
    def serialize_term_queries(self):
        s = []
        for name, value_set in sorted(self.terms.items()):
            if name:
                field = self.schema.match_field(name)
            else:
                field = self.schema.default_field
            if isinstance(field, SolrUnicodeField):
                value_set = [self.__lqs_escape(value) for value in value_set]
            if name:
                s += [u'%s:%s' % (name, value) for value in sorted(value_set)]
            else:
                s += sorted(value_set)
        return ' AND '.join(s)

    # I'm very much not sure we're doing the right thing here:
    lucene_special_chars = re.compile(r'([+\-&|!\(\){}\[\]\^\"~\*\?:\\])')
    lucene_special_words = ("AND", "NOT", "OR")
    def __lqs_escape(self, s):
        if s in self.lucene_special_words:
            return u'"%s"'%s
        return self.lucene_special_chars.sub(r'\\\1', s)

    def serialize_phrase_queries(self):
        s = []
        for name, value_set in sorted(self.phrases.items()):
            if name:
                field = self.schema.match_field(name)
            else:
                field = self.schema.default_field
            if isinstance(field, SolrUnicodeField):
                value_set = [self.__phrase_escape(value) for value in value_set]
            if name:
                s += [u'%s:"%s"' % (name, value)
                      for value in sorted(value_set)]
            else:
                s += ['"%s"' % value for value in sorted(value_set)]
        return ' AND '.join(s)

    def __phrase_escape(self, s):
        # For phrases, anything is allowed between double-quotes, except
        # double-quotes themselves, which must be escaped with backslashes,
        # and thus also backslashes must too be escaped.
        return s.replace('\\', '\\\\').replace('"', '\\"')

    def serialize_range_queries(self):
        s = []
        for name, rel, value in sorted(self.ranges):
            range = self.range_query_templates[rel] % value
            s.append("%(name)s:%(range)s" % vars())
        return ' AND '.join(s)

    def child_needs_parens(self, child, op=None):
        if child.is_single_query():
            return False
        elif child._not is not None or child._pow is not None:
            return False
        elif (self._or is not None or op=='OR') and child._or is not None:
            return False
        elif (self._and is not None or op=='AND') and child._and is not None:
            return False
        else:
            return True

    def __unicode__(self):
        if self._or is not None:
            s = []
            for o in self._or:
                if self.child_needs_parens(o):
                    s.append(u"(%s)"%o)
                else:
                    s.append(u"%s"%o)
            return u" OR ".join(s)
        elif self._and is not None:
            s = []
            for o in self._and:
                if self.child_needs_parens(o):
                    s.append(u"(%s)"%o)
                else:
                    s.append(u"%s"%o)
            return u" AND ".join(s)
        elif self._not is not None:
            o = self._not
            if o._not is not None:
                # they cancel out
                self._not = self._not._not
                return u"%s"%o
            if self.child_needs_parens(o):
                return u"NOT (%s)"%o
            else:
                return u"NOT %s"%o
        elif self._pow is not None:
            q, v = self._pow
            if self.child_needs_parens(q):
                return u"(%s)^%s"%(q,v)
            else:
                return u"%s^%s"%(q,v)
        else:
            u = [s for s in [self.serialize_term_queries(),
                             self.serialize_phrase_queries(),
                             self.serialize_range_queries()]
                 if s]
            if not u and len(self.subqueries) == 1:
                # Only one subquery, no need for parens
                u.append(u"%s"%self.subqueries[0])
            else:
                for q in self.subqueries:
                    if self.child_needs_parens(q, 'AND'):
                        u.append(u"(%s)"%q)
                    else:
                        u.append(u"%s"%q)
            return ' AND '.join(u)

    def stringify_with_optional_parens(self):
        if self.is_single_query():
            s = u"%s"
        else:
            s = u"(%s)"
        return s % self

    def is_single_query(self):
        return sum([len(self.terms), len(self.phrases), len(self.ranges), len(self.subqueries)]) == 1

    def Q(self):
        return LuceneQuery(self.schema)

    def __nonzero__(self):
        return bool(self.terms) or bool(self.phrases) or bool(self.ranges) or bool(self.subqueries)

    def __or__(self, other):
        q = LuceneQuery(self.schema)
        q._or = (self, other)
        return q

    def __and__(self, other):
        q = LuceneQuery(self.schema)
        q._and = (self, other)
        return q

    def __invert__(self):
        q = LuceneQuery(self.schema)
        q._not = self
        return q

    def __pow__(self, value):
        try:
            float(value)
        except ValueError:
            raise ValueError("Non-numeric value supplied for boost")
        q = LuceneQuery(self.schema)
        q._pow = (self, value)
        return q
        
    def add(self, args, kwargs, terms_or_phrases=None):
        _args = []
        for arg in args:
            if isinstance(arg, LuceneQuery):
                self.subqueries.append(arg)
            else:
                _args.append(arg)
        args = _args
        try:
            terms_or_phrases = kwargs.pop("__terms_or_phrases")
        except KeyError:
            terms_or_phrases = None
        for value in args:
            self.add_exact(None, value, terms_or_phrases)
        for k, v in kwargs.items():
            try:
                field_name, rel = k.split("__")
            except ValueError:
                field_name, rel = k, 'eq'
            field = self.schema.match_field(field_name)
            if not field:
                raise ValueError("%s is not a valid field name" % k)
            if rel == 'eq':
                self.add_exact(field_name, v, terms_or_phrases)
            else:
                self.add_range(field_name, rel, v)

    def add_exact(self, field_name, value, term_or_phrase):
        if field_name:
            field = self.schema.match_field(field_name)
        else:
            field = self.schema.default_field
        values = field.serialize(value) # Might be multivalued
        if isinstance(values, basestring):
            values = [values]
        for value in values:
            if isinstance(field, SolrUnicodeField):
                this_term_or_phrase = term_or_phrase or self.term_or_phrase(value)
            else:
                this_term_or_phrase = "terms"
            getattr(self, this_term_or_phrase)[field_name].add(value)

    def add_range(self, field_name, rel, value):
        field = self.schema.match_field(field_name)
        if isinstance(field, SolrBooleanField):
            raise ValueError("Cannot do a '%s' query on a bool field" % rel)
        if rel not in self.range_query_templates:
            raise SolrError("No such relation '%s' defined" % rel)
        if rel in ('range', 'rangeexc'):
            try:
                assert len(value) == 2
            except (AssertionError, TypeError):
                raise SolrError("'%s__%s' argument must be a length-2 iterable"
                                 % (field_name, rel))
            value = tuple(sorted(field.serialize(v) for v in value))
        else:
            value = field.serialize(value)
        self.ranges.add((field_name, rel, value))

    def term_or_phrase(self, arg, force=None):
        return 'terms' if self.default_term_re.match(arg) else 'phrases'


class SolrSearch(object):
    option_modules = ('query_obj', 'filter_obj', 'paginator', 'more_like_this', 'highlighter', 'faceter', 'sorter')
    def __init__(self, interface, original=None):
        self.interface = interface
        self.schema = interface.schema
        if original is None:
            self.query_obj = LuceneQuery(self.schema, 'q')
            self.filter_obj = LuceneQuery(self.schema, 'fq')
            self.paginator = PaginateOptions(self.schema)
            self.more_like_this = MoreLikeThisOptions(self.schema)
            self.highlighter = HighlightOptions(self.schema)
            self.faceter = FacetOptions(self.schema)
            self.sorter = SortOptions(self.schema)
        else:
            for opt in self.option_modules:
                setattr(self, opt, getattr(original, opt).clone())

    def clone(self):
        return SolrSearch(interface=self.interface, original=self)

    def Q(self, *args, **kwargs):
        q = LuceneQuery(self.schema)
        q.add(args, kwargs)
        return q

    def query_by_term(self, *args, **kwargs):
        return self.query(__terms_or_phrases="terms", *args, **kwargs)

    def query_by_phrase(self, *args, **kwargs):
        return self.query(__terms_or_phrases="phrases", *args, **kwargs)

    def filter_by_term(self, *args, **kwargs):
        return self.filter(__terms_or_phrases="terms", *args, **kwargs)

    def filter_by_phrase(self, *args, **kwargs):
        return self.filter(__terms_or_phrases="phrases", *args, **kwargs)

    def query(self, *args, **kwargs):
        newself = self.clone()
        newself.query_obj.add(args, kwargs)
        return newself

    def exclude(self, *args, **kwargs):
        newself = self.clone()
        newself.query(~newself.Q(*args, **kwargs))
        return newself

    def filter(self, *args, **kwargs):
        newself = self.clone()
        newself.filter_obj.add(args, kwargs)
        return newself

    def filter_exclude(self, *args, **kwargs):
        newself = self.clone()
        newself.filter(~newself.Q(*args, **kwargs))
        return newself

    def facet_by(self, field, **kwargs):
        newself = self.clone()
        newself.faceter.update(field, **kwargs)
        return newself

    def highlight(self, fields=None, **kwargs):
        newself = self.clone()
        newself.highlighter.update(fields, **kwargs)
        return newself

    def mlt(self, fields, query_fields=None, **kwargs):
        newself = self.clone()
        newself.more_like_this.update(fields, query_fields, **kwargs)
        return newself

    def paginate(self, start=None, rows=None):
        newself = self.clone()
        newself.paginator.update(start, rows)
        return newself

    def sort_by(self, field):
        newself = self.clone()
        newself.sorter.update(field)
        return newself

    def boost_relevancy(self, boost_score, **kwargs):
        if not self.query_obj:
            raise TypeError("Can't boost the relevancy of an empty query")
        try:
            float(boost_score)
        except ValueError:
            raise ValueError("Non-numeric boost value supplied")

        # Clone all of self *except* query, which we'll take care of directly
        newself = self.clone()
        newself.query_obj = LuceneQuery(self.schema, 'q')

        return newself.query(self.query_obj |
                             (self.query_obj & self.Q(**kwargs)**boost_score))

    def options(self):
        options = {}
        for option_module in self.option_modules:
            options.update(getattr(self, option_module).options)
        return options

    def params(self):
        return self.interface.params(**self.options())

    def execute(self, constructor=dict):
        result = self.interface.search(**self.options())
        if constructor is not dict:
            result.result.docs = [constructor(**d) for d in result.result.docs]
        return result


class Options(object):
    def clone(self):
        return self.__class__(self.schema, self)

    def invalid_value(self, msg=""):
        assert False, msg

    def update(self, fields=None, **kwargs):
        if fields:
            self.schema.check_fields(fields)
            if isinstance(fields, basestring):
                fields = [fields]
            for field in set(fields) - set(self.fields):
                self.fields[field] = {}
        elif kwargs:
            fields = [None]
        self.check_opts(fields, kwargs)

    def check_opts(self, fields, kwargs):
        for k, v in kwargs.items():
            if k not in self.opts:
                raise SolrError("No such option for %s: %s" % (self.option_name, k))
            opt_type = self.opts[k]
            try:
                if isinstance(opt_type, (list, tuple)):
                    assert v in opt_type
                elif isinstance(opt_type, type):
                    v = opt_type(v)
                else:
                    v = opt_type(self, v)
            except:
                raise SolrError("Invalid value for %s option %s: %s" % (self.option_name, k, v))
            for field in fields:
                self.fields[field][k] = v

    @property
    def options(self):
        opts = {}
        if self.fields:
            opts[self.option_name] = True
            fields = [field for field in self.fields if field]
            self.field_names_in_opts(opts, fields)
        for field_name, field_opts in self.fields.items():
            if not field_name:
                for field_opt, v in field_opts.items():
                    opts['%s.%s'%(self.option_name, field_opt)] = v
            else:
                for field_opt, v in field_opts.items():
                    opts['f.%s.%s.%s'%(field_name, self.option_name, field_opt)] = v
        return opts



class FacetOptions(Options):
    option_name = "facet"
    opts = {"prefix":unicode,
            "sort":[True, False, "count", "index"],
            "limit":int,
            "offset":lambda self, x: int(x) >= 0 and int(x) or self.invalid_value(),
            "mincount":lambda self, x: int(x) >= 0 and int(x) or self.invalid_value(),
            "missing":bool,
            "method":["enum", "fc"],
            "enum.cache.minDf":int,
            }

    def __init__(self, schema, original=None):
        self.schema = schema
        if original is None:
            self.fields = collections.defaultdict(dict)
        else:
            self.fields = copy.copy(original.fields)

    def field_names_in_opts(self, opts, fields):
        if fields:
            opts["facet.field"] = sorted(fields)


class HighlightOptions(Options):
    option_name = "hl"
    opts = {"snippets":int,
            "fragsize":int,
            "mergeContinuous":bool,
            "requireFieldMatch":bool,
            "maxAnalyzedChars":int,
            "alternateField":lambda self, x: x if x in self.schema.fields else self.invalid_value(),
            "maxAlternateFieldLength":int,
            "formatter":["simple"],
            "simple.pre":unicode,
            "simple.post":unicode,
            "fragmenter":unicode,
            "usePhraseHighlighter":bool,
            "highlightMultiTerm":bool,
            "regex.slop":float,
            "regex.pattern":unicode,
            "regex.maxAnalyzedChars":int
            }
    def __init__(self, schema, original=None):
        self.schema = schema
        if original is None:
            self.fields = collections.defaultdict(dict)
        else:
            self.fields = copy.copy(original.fields)

    def field_names_in_opts(self, opts, fields):
        if fields:
            opts["hl.fl"] = ",".join(sorted(fields))


class MoreLikeThisOptions(Options):
    opts = {"count":int,
            "mintf":int,
            "mindf":int,
            "minwl":int,
            "maxwl":int,
            "maxqt":int,
            "maxntp":int,
            "boost":bool,
            }
    def __init__(self, schema, original=None):
        self.schema = schema
        if original is None:
            self.fields = set()
            self.query_fields = {}
            self.kwargs = {}
        else:
            self.fields = copy.copy(original.fields)
            self.query_fields = copy.copy(original.query_fields)
            self.kwargs = copy.copy(original.kwargs)

    def update(self, fields, query_fields=None, **kwargs):
        self.schema.check_fields(fields)
        if isinstance(fields, basestring):
            fields = [fields]
        self.fields.update(fields)

        if query_fields is not None:
            for k, v in query_fields.items():
                if k not in self.fields:
                    raise SolrError("'%s' specified in query_fields but not fields"% k)
                if v is not None:
                    try:
                        v = float(v)
                    except ValueError:
                        raise SolrError("'%s' has non-numerical boost value"% k)
            self.query_fields.update(query_fields)

        for opt_name, opt_value in kwargs.items():
            if opt_name not in self.opts:
                raise SolrError("Invalid MLT option %s" % opt_name)
            opt_type = self.opts[opt_name]
            try:
                opt_type(opt_value)
            except (ValueError, TypeError):
                raise SolrError("'mlt.%s' should be an '%s'"%
                                (opt_name, opt_type.__name__))
        self.kwargs.update(kwargs)

    @property
    def options(self):
        opts = {}
        if self.fields:
            opts['mlt'] = True
            opts['mlt.fl'] = ','.join(sorted(self.fields))

        if self.query_fields:
            qf_arg = []
            for k, v in self.query_fields.items():
                if v is None:
                    qf_arg.append(k)
                else:
                    qf_arg.append("%s^%s" % (k, float(v)))
            opts["mlt.qf"] = " ".join(qf_arg)

        for opt_name, opt_value in self.kwargs.items():
            opt_type = self.opts[opt_name]
            opts["mlt.%s" % opt_name] = opt_type(opt_value)

        return opts


class PaginateOptions(Options):
    def __init__(self, schema, original=None):
        self.schema = schema
        if original is None:
            self.start = None
            self.rows = None
        else:
            self.start = original.start
            self.rows = original.rows

    def update(self, start, rows):
        if start is not None:
            if start < 0:
                raise SolrError("paginator start index must be 0 or greater")
            self.start = start
        if rows is not None:
            if rows < 0:
                raise SolrError("paginator rows must be 0 or greater")
            self.rows = rows

    @property
    def options(self):
        opts = {}
        if self.start is not None:
            opts['start'] = self.start
        if self.rows is not None:
            opts['rows'] = self.rows
        return opts


class SortOptions(Options):
    option_name = "sort"
    def __init__(self, schema, original=None):
        self.schema = schema
        if original is None:
            self.fields = []
        else:
            self.fields = copy.copy(original.fields)

    def update(self, field):
        # We're not allowing function queries a la Solr1.5
        if field.startswith('-'):
            order = "desc"
            field = field[1:]
        elif field.startswith('+'):
            order = "asc"
            field = field[1:]
        else:
            order = "asc"
        if field != 'score':
            f = self.schema.match_field(field)
            if not f:
                raise SolrError("No such field %s" % field)
            elif f.multi_valued:
                raise SolrError("Cannot sort on a multivalued field")
            elif not f.indexed:
                raise SolrError("Cannot sort on an un-indexed field")
        self.fields.append([order, field])

    @property
    def options(self):
        if self.fields:
            return {"sort":", ".join("%s %s" % (field, order) for order, field in self.fields)}
        else:
            return {}
