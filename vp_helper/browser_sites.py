"""Explicit judge aliases and first-party cookie scopes for native browsers."""
BASES = {'cf': ('codeforces.com',), 'gym': ('codeforces.com',),
         'qoj': ('qoj.ac', 'contest.ucup.ac'), 'cwnuoj': ('oj.cwnupaa.com',),
         'nowcoder': ('ac.nowcoder.com',)}
PARENTS = {'oj.cwnupaa.com': 'cwnupaa.com', 'ac.nowcoder.com': 'nowcoder.com'}
HOSTS = frozenset(host for bases in BASES.values() for base in bases
                  for root in (base, PARENTS.get(base, base)) for host in (root, 'www.' + root))
DOMAINS = frozenset(domain for host in HOSTS for domain in (host, '.' + host))


def canonical_host(host):
    return host.removeprefix('www.')


def group_domains(base):
    return {domain for root in (base, PARENTS.get(base, base))
            for host in (root, 'www.' + root) for domain in (host, '.' + host)}


def first_party(cookie):
    key = cookie.get('partitionKey')
    if key is None:
        return True
    host = canonical_host(cookie.get('domain', '').lstrip('.'))
    site = 'https://' + '.'.join(host.split('.')[-2:])
    return isinstance(key, dict) and key.get('topLevelSite') == site and key.get('hasCrossSiteAncestor') is not True
