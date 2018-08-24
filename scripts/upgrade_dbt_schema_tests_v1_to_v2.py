#! /usr/bin/env python

from argparse import ArgumentParser
import logging
import sys
import yaml

LOGGER = logging.getLogger('upgrade_dbt_schema')
LOGFILE = 'upgrade_dbt_schema_tests_v1_to_v2.txt'

# compatibility nonsense
try:
    basestring = basestring
except NameError:
    basestring = str


class OperationalError(Exception):
    pass


def setup_loging(filename):
    logging.basicConfig(filename=filename, level=logging.DEBUG)


def parse_args(args):
    parser = ArgumentParser(description='dbt schema converter')
    parser.add_argument(
        '--logfile-path',
        dest='logfile_path',
        help='The path to write the logfile to',
        default=LOGFILE
    )
    parser.add_argument(
        '--overwrite',
        action='store_true',
        help='if set, overwrite any existing file'
    )
    parser.add_argument(
        '--in-place',
        action='store_true',
        dest='in_place',
        help=('if set, overwrite the input file and generate a ".bak" file '
              'instead of generating a ".new" file')
    )
    parser.add_argument('--output-path', dest='output_path', default=None)
    parser.add_argument('--backup-path', dest='backup_path', default=None)
    parser.add_argument('input-path', dest='input_path')
    parsed = parser.parse_args()
    if parsed.in_place:
        parsed.overwrite = True
    if parsed.output_path is None:
        if parsed.in_place:
            parsed.output_path = parsed.input_path
            parsed.backup_path = parsed.input_path + '.bak'
        else:
            parsed.output_path = parsed.input_path + '.new'
    return parsed


def backup_file(src, dst, overwrite):
    if not overwrite and os.path.exists(dst):
        raise OperationalError(
            'backup file at {} already exists and --overwrite was not passed'
            .format(dst)
        )
    with open(src, 'rb'), open(dst, 'wb') as ifp, ofp:
        ofp.write(ifp.read())


def handle(parsed):
    """Try to handle the schema conversion. On failure, raise OperationalError
    and let the caller handle it.
    """
    if parsed.backup_path:
        backup_file(parsed.output_path, parsed.backup_path, parsed.overwrite)
    if not os.path.exists(parsed.input_path):
        LOGGER.error('input file at {} does not exist!'.format(parsed.input_path))
    with open(parsed_args.input_path) as fp:
        initial = yaml.safe_load(fp)
    version = initial.get('version', 1)
    # the isinstance check is to handle the case of models named 'version'
    if version != 1 and isinstance(version, int):
        LOGGER.error('input file is not a v1 yaml file (reports as {})'
                    .format(version))
        return
    if os.path.exists(output_path) and not overwrite:
        LOGGER.error('output file at {} already exists, and --overwrite was not passed')
        return
    new_file = convert_schema(initial)
    with open(parsed.output_path, 'w') as fp:
        yaml.safe_dump(new_file, fp)


def main(args=None):
    if args is None:
        args = sys.argv[1:]

    parsed = parse_args(args)
    setup_logging(parsed.logfile_path)
    try:
        handle(parsed)
    except OperationalError as exc:
        LOGGER.error(exc.message)
    except:
        LOGGER.exception('Fatal error during conversion attempt')
    else:
        LOGGER.info('successfully converted existing {} to {}'.format(
            parsed.input_path, parsed.output_path
        ))


def sort_keyfunc(item):
    if isinstance(item, basestring):
        return item
    else:
        return list(item)[0]


def sorted_column_list(column_dict):
    columns = []
    for column in sorted(column_dict.values(), key=lambda c: c['name']):
        # make the unit tests a lot nicer. This is horrible, sorry.
        column['tests'].sort(key=sort_keyfunc)
        columns.append(column)
    return columns


class ModelTestBuilder(object):
    SIMPLE_COLUMN_TESTS = {'unique', 'not_null'}
    # map test name -> the key that indicates column name
    COMPLEX_COLUMN_TESTS = {
        'relationships': 'from',
        'accepted_values': 'field',
    }
    def __init__(self, model_name):
        self.model_name = model_name
        self.columns = {}
        self.model_tests = []

    def get_column(self, column_name):
        if column_name in self.columns:
            return self.columns[column_name]
        column = {'name': column_name, 'tests': []}
        self.columns[column_name] = column
        return column

    def add_test(self, column_name, test_name):
        column = self.get_column(column_name)
        column['tests'].append(test_name)

    def handle_simple_column(self, test_values, test_name):
        for column_name in test_values:
            self.add_test(column_name, test_name)

    def handle_complex_column(self, test_values, test_name):
        """'complex' columns are lists of dicts, where each dict has a single
        key (the test name) and the value of that key is a dict of test values.
        """
        column_key = self.COMPLEX_COLUMN_TESTS[test_name]
        for dct in test_values:
            if column_key not in dct:
                raise OperationalError(
                    'got an invalid {} test in model {}, no "{}" value in {}'
                    .format(test_name, self.model_name, column_key, dct)
                )
            column_name = dct[column_key]
            # for syntax nice-ness reasons, we define these tests as single-key
            # dicts where the key is the test name.
            test_value = {k: v for k, v in dct.items() if k != column_key}
            value = {test_name: test_value}

            self.add_test(column_name, value)

    def populate_test(self, test_name, test_values):
        # if test_values isn't a list, this is not an ok schema
        if not isinstance(test_values, list):
            raise OperationalError(
                'Expected type "list" for test values in constraints '
                'under test {} inside model {}, got "{}"'.format(
                    test_name, model_name, type(test_values)
                )
            )
        if test_name in self.SIMPLE_COLUMN_TESTS:
            self.handle_simple_column(test_values, test_name)
        elif test_name in self.COMPLEX_COLUMN_TESTS:
            # import ipdb;ipdb.set_trace()
            self.handle_complex_column(test_values, test_name)
        else:
            ## TODO: implement...
            raise ValueError('cannot handle any other tests')

    def populate_from_constraints(self, constraints):
        for test_name, test_values in constraints.items():
            self.populate_test(test_name, test_values)

    def generate_model_dict(self):
        model = {'name': self.model_name}
        if self.model_tests:
            model['tests'] = self.model_tests

        if self.columns:
            model['columns'] = sorted_column_list(self.columns)
        return model


def convert_schema(initial):
    models = []

    for model_name, model_data in initial.items():
        if 'constraints' not in model_data:
            # don't care about this model
            continue
        builder = ModelTestBuilder(model_name)
        builder.populate_from_constraints(model_data['constraints'])
        model = builder.generate_model_dict()
        models.append(model)

    return {
        'version': 2,
        'models': models,
    }



if __name__ == '__main__':
    main()

import unittest

SAMPLE_SCHEMA = '''
foo:
    constraints:
        not_null:
            - id
            - email
            - favorite_color
        unique:
            - id
            - email
        accepted_values:
            - { field: favorite_color, values: ['blue', 'green'] }
            - { field: likes_puppies, values: ['yes'] }

bar:
    constraints:
        not_null:
            - id
'''




class TestConvert(unittest.TestCase):
    maxDiff = None
    def test_convert(self):
        input_schema = yaml.safe_load(SAMPLE_SCHEMA)
        output_schema = convert_schema(input_schema)
        self.assertEqual(output_schema['version'], 2)
        sorted_models = sorted(output_schema['models'], key=lambda x: x['name'])
        expected = [
            {
                'name': 'bar',
                'columns': [
                    {
                        'name': 'id',
                        'tests': [
                            'not_null'
                        ]
                    }
                ]
            },
            {
                'name': 'foo',
                'columns': [
                    {
                        'name': 'email',
                        'tests': ['not_null', 'unique'],
                    },
                    {
                        'name': 'favorite_color',
                        'tests': [
                            {'accepted_values': {'values': ['blue', 'green']}},
                            'not_null'
                        ],
                    },
                    {
                        'name': 'id',
                        'tests': ['not_null', 'unique'],
                    },
                    {
                        'name': 'likes_puppies',
                        'tests': [{'accepted_values': {'values': ['yes']}}]
                    },
                ],
            },
        ]
        self.assertEqual(sorted_models, expected)
