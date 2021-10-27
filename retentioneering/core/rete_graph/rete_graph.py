# * Copyright (C) 2020 Maxim Godzi, Anatoly Zaytsev, Retentioneering Team
# * This Source Code Form is subject to the terms of the Retentioneering Software Non-Exclusive License (License)
# * By using, sharing or editing this code you agree with the License terms and conditions.
# * You can obtain License text at https://github.com/retentioneering/retentioneering-tools/blob/master/LICENSE.md


from typing import Any, MutableSequence, Sequence, Mapping, Callable, Type, Optional, Union, MutableMapping, cast
from typing_extensions import Literal, TypedDict
from pandas import DataFrame, Series
from IPython.display import IFrame, display, HTML
from retentioneering.utils.jupyter_server.server import ServerManager, JupyterServer
from . import templates
from datetime import datetime
import networkx as nx
import json
import random
import string

Threshold = MutableMapping[str, float]
Position = MutableMapping[str, Sequence[float]]
NodeParams = MutableMapping[str, str]


class Degree(TypedDict):
    degree: float
    source: float


class PreparedNode(TypedDict):
    index: int
    name: str
    degree: MutableMapping[str, Degree]
    changed_name: Optional[str]
    type: str
    x: Optional[float]
    y: Optional[float]
    active: bool
    alias: str
    parent: str


class Weight(TypedDict):
    weight_norm: float
    weight: float


class PreparedLink(TypedDict):
    sourceIndex: int
    targetIndex: int
    weights: MutableMapping[str, Weight]
    type: str


class SpringLayoutConfig(TypedDict):
    k: float
    iterations: int
    nx_threshold: float


class LayoutNode(TypedDict):
    name: str
    x: float
    y: float


class GraphSettings(TypedDict, total=False):
    show_weights: bool
    show_percents: bool
    show_nodes_names: bool
    show_all_edges_for_targets: bool
    show_nodes_without_links: bool
    nodes_threshold: Threshold
    links_threshold: Threshold


def generateId(size=6, chars=string.ascii_uppercase + string.digits):
    return "el" + ''.join(random.choice(chars) for _ in range(size))


class ReteGraph():
    from retentioneering.core.rete_explorer.rete_explorer import ReteExplorer
    rete: ReteExplorer
    clickstream: DataFrame
    nodelist: DataFrame
    edgelist: DataFrame
    server: JupyterServer
    env: Literal['colab', 'classic']
    spring_layout_config: SpringLayoutConfig
    layout: Optional[DataFrame]
    graph_settings: GraphSettings

    def __init__(
        self,
        rete: ReteExplorer,
        clickstream: DataFrame,
        nodelist: DataFrame,
        edgelist: DataFrame,
        layout: DataFrame = None,
        graph_settings: GraphSettings = {},
    ):
        sm = ServerManager()
        self.env = sm.check_env()
        self.server = sm.create_server()
        self.rete = rete
        self.clickstream = clickstream
        self.nodelist = nodelist.copy()
        self.edgelist = edgelist.copy()
        self.spring_layout_config = {
            "k": .1,
            "iterations": 300,
            "nx_threshold": 1e-4
        }

        self.layout = layout
        self.graph_settings = graph_settings

        self.server.use("save-nodelist",
                        lambda n: self._on_nodelist_updated(n))
        self.server.use("recalculate", lambda n: self._on_recalc_request(n))
        self.server.use("save-layout", lambda n: self._on_layout_request(n))
        self.server.use("save-graph-settings",
                        lambda n: self._on_graph_settings_request(n))

    def _on_graph_settings_request(self, settings: GraphSettings):
        self.graph_settings = settings

    def _on_layout_request(self, layout_nodes: MutableSequence[LayoutNode]):
        self.graph_updates = layout_nodes
        self.layout = DataFrame(columns=["name", "x", "y"])
        for layout_node in layout_nodes:
            self.layout.loc[self.layout.shape[0]] = [
                layout_node["name"], layout_node["x"], layout_node["y"]]

    def _on_recalc_request(self, nodes: MutableSequence[PreparedNode]):
        self.updates = nodes
        self._on_nodelist_updated(nodes)
        self._recalculate()

        self.edgelist["type"] = 'suit'

        nodes, nodes_set = self._prepare_nodes(
            nodelist=self.nodelist,
        )
        links = self._prepare_edges(
            edgelist=self.edgelist, nodes_set=nodes_set)
        result = {
            "nodes": nodes,
            "links": links,
        }

        return result

    def _replace_grouped_events(self, grouped: Series, row):
        event_col = self.rete.config["event_col"]
        event_name = row[event_col]
        mathced = grouped[grouped[event_col] == event_name]

        if (len(mathced) > 0):
            parent_node_name = mathced.iloc[0]['parent']
            row[event_col] = parent_node_name

        return row

    def _update_node_after_recalc(self, recalculated_nodelist: DataFrame, row):
        cols = self.rete.get_nodelist_cols()
        event_col = self.rete.config["event_col"]
        node_name = row[event_col]
        mathced: Series[Any] = recalculated_nodelist[recalculated_nodelist[event_col] == node_name]

        if (len(mathced) > 0):
            recalculated_node = mathced.iloc[0]
            for col in cols:
                row[col] = recalculated_node[col]
        return row.copy()

    def _recalculate(self):
        event_col = self.rete.config["event_col"]
        curr_nodelist = self.nodelist
        active = curr_nodelist[curr_nodelist['active'] == True]
        grouped = curr_nodelist[~curr_nodelist['parent'].isnull(
        ) & curr_nodelist['active'] == True]

        updated_clickstream = self.rete.clickstream.copy()
        # remove disabled events
        updated_clickstream = updated_clickstream[updated_clickstream[event_col].isin(
            active[event_col])]
        # recalculate
        updated_clickstream = updated_clickstream.apply(
            lambda x: self._replace_grouped_events(grouped, x), axis=1)

        # save norm type
        recalculated_nodelist = self.rete.create_nodelist(updated_clickstream)
        recalculated_edgelist = self.rete.create_edgelist(
            clickstream=updated_clickstream)

        self.nodelist = curr_nodelist.apply(
            lambda x: self._update_node_after_recalc(recalculated_nodelist, x), axis=1)
        self.edgelist = recalculated_edgelist

    def _on_nodelist_updated(self, nodes: MutableSequence[PreparedNode]):
        self.updates = nodes
        event_col = self.rete.config["event_col"]
        nodelist = self.nodelist
        for node in nodes:
            indexes = nodelist.index[nodelist[event_col]
                                     == node['name']].tolist()
            index = indexes[0] if len(indexes) > 0 else None

            if index is not None:
                nodelist.at[index, "active"] = node["active"]
                nodelist.at[index, "parent"] = node["parent"]
                if "changed_name" in node:
                    nodelist.at[index, "changed_name"] = node["changed_name"]
                for col, value in node['degree'].items():
                    nodelist.at[index, col] = value["source"]
            else:
                row: MutableSequence[Any] = [None] * len(nodelist.columns)

                for i in range(len(nodelist.columns.tolist())):
                    key = nodelist.columns[i]
                    if key == event_col:
                        row[i] = node["name"]
                    elif key == "active":
                        row[i] = node["active"]
                    elif key == "alias":
                        row[i] = node["alias"]
                    elif key == "parent":
                        row[i] = node["parent"]
                    elif key == "changed_name":
                        if "changed_name" in node:
                            row[i] = node["changed_name"]
                        else:
                            row[i] = None
                    elif key in node["degree"]:
                        row[i] = node["degree"][key]["source"]

                nodelist.loc[node["index"]] = row

    def _make_node_params(self, targets: MutableMapping[str, str] = None):
        if targets is not None:
            for k, v in targets.items():
                if v == 'red':
                    v = 'bad_target'
                if v == 'green':
                    v = 'nice_target'
                targets[k] = v
            return targets
        else:
            _node_params = {
                'positive_target_event': 'nice_target',
                'negative_target_event': 'bad_target',
                'source_event': 'source',
            }
            node_params: NodeParams = {}
            for key, val in _node_params.items():
                name = self.rete.config.get(key)
                if name is None:
                    continue
                node_params.update({name: val})
            return node_params

    def _get_norm_link_threshold(self, links_threshold: Threshold = None):
        nodelist_default_col = self.rete.get_nodelist_default_col()
        edgelist_default_col = self.rete.get_edgelist_default_col()
        scale = float(
            cast(float, self.edgelist[edgelist_default_col].abs().max()))
        norm_links_threshold = None

        if links_threshold is not None:
            norm_links_threshold = {}
            for key in links_threshold:
                if key == nodelist_default_col:
                    norm_links_threshold[nodelist_default_col] = links_threshold[nodelist_default_col] / scale
                else:
                    s = float(cast(float, self.edgelist[key].abs().max()))
                    norm_links_threshold[key] = links_threshold[key] / s
        return norm_links_threshold

    def _get_norm_node_threshold(self, nodes_threshold: Threshold = None):
        norm_nodes_threshold = None
        if nodes_threshold is not None:
            norm_nodes_threshold = {}
            for key in nodes_threshold:
                scale = float(cast(float, self.nodelist[key].abs().max()))
                norm_nodes_threshold[key] = nodes_threshold[key] / scale

        return norm_nodes_threshold

    def _calc_layout(self, edgelist: DataFrame, width: int, height: int):
        G = nx.DiGraph()
        source_col = edgelist.columns[0]
        target_col = edgelist.columns[1]
        weight_col = edgelist.columns[2]

        G.add_weighted_edges_from(
            edgelist.loc[:, [source_col, target_col, weight_col]].values)

        pos_new = nx.layout.spring_layout(G, k=self.spring_layout_config["k"],
                                          iterations=self.spring_layout_config["iterations"],
                                          threshold=self.spring_layout_config["nx_threshold"],
                                          seed=0)

        min_x = min([j[0] for i, j in pos_new.items()])
        min_y = min([j[1] for i, j in pos_new.items()])
        max_x = max([j[0] for i, j in pos_new.items()])
        max_y = max([j[1] for i, j in pos_new.items()])

        pos_new: Position = {
            i: [(j[0] - min_x) / (max_x - min_x) * (width - 150) + 75,
                (j[1] - min_y) / (max_y - min_y) * (height - 100) + 50]
            for i, j in pos_new.items()
        }
        return pos_new

    def _prepare_nodes(self, nodelist: DataFrame, node_params: NodeParams = None, pos: Position = None):
        node_names = set(nodelist[self.rete.config["event_col"]])
        event_col = self.rete.config["event_col"]
        cols = self.rete.get_nodelist_cols()

        nodes_set: MutableMapping[str, PreparedNode] = {}
        for idx, node_name in enumerate(node_names):
            row = nodelist.loc[nodelist[event_col] == node_name]
            degree = {}
            for weight_col in cols:
                max_degree = cast(float, nodelist[weight_col].max())
                r = row[weight_col]
                r = r.tolist()
                value = r[0]
                curr_degree = {}
                curr_degree["degree"] = (abs(value)) / abs(max_degree) * 30 + 4
                curr_degree["source"] = value
                degree[weight_col] = curr_degree

            node_pos = pos.get(node_name) if pos is not None else None
            active = cast(bool, row["active"].tolist()[0])
            alias = cast(str, row["alias"].to_list()[0])
            parent = cast(str, row["parent"].to_list()[0])

            type = node_params.get(
                node_name) or "suit" if node_params is not None else "suit"

            node: PreparedNode = {
                "index": idx,
                "name": node_name,
                "degree": degree,
                "type": type + "_node",
                "active": active,
                "alias": alias,
                "parent": parent,
                "changed_name": None,
                "x": None,
                "y": None,
            }

            if node_pos is not None:
                node["x"] = node_pos[0]
                node["y"] = node_pos[1]

            nodes_set.update({node_name: node})

        return list(nodes_set.values()), nodes_set

    def _prepare_edges(self, edgelist: DataFrame, nodes_set: MutableMapping[str, PreparedNode]):
        default_col = self.rete.get_nodelist_default_col()
        weight_col = edgelist.columns[2]
        source_col = edgelist.columns[0]
        target_col = edgelist.columns[1]
        custom_cols = self.rete.get_custom_cols()
        edges: MutableSequence[PreparedLink] = []

        edgelist['weight_norm'] = edgelist[weight_col] / \
            edgelist[weight_col].abs().max()

        for _, row in edgelist.iterrows():
            default_col_weight: Weight = {
                "weight_norm": row.weight_norm,
                "weight": cast(float, row[weight_col]),
            }
            weights = {
                default_col: default_col_weight,
            }
            for custom_weight_col in custom_cols:
                weight = cast(float, row[custom_weight_col])
                max_weight = cast(
                    float, edgelist[custom_weight_col].abs().max())
                weight_norm = weight / max_weight
                col_weight: Weight = {
                    "weight_norm": weight_norm,
                    "weight": cast(float, row[custom_weight_col]),
                }
                weights[custom_weight_col] = col_weight

            source_node_name = cast(str, row[source_col])
            target_node_name = cast(str, row[target_col])

            source_node = nodes_set.get(source_node_name)
            target_node = nodes_set.get(target_node_name)

            if source_node is not None:
                if target_node is not None:
                    edges.append({
                        "sourceIndex": source_node["index"],
                        "targetIndex": target_node["index"],
                        "weights": weights,
                        "type": cast(str, row['type'])
                    })

        return edges

    def _make_template_data(self, node_params: NodeParams, width: int, height: int):
        edgelist = self.edgelist.copy()
        nodelist = self.nodelist.copy()

        source_col = edgelist.columns[0]
        target_col = edgelist.columns[1]

        # calc edge type
        edgelist["type"] = edgelist.apply(
            lambda x: node_params.get(x[source_col]) if node_params.get(x[source_col]) == 'source' else node_params.get(
                x[target_col]) or 'suit', 1)

        pos = self._use_layout(
            self._calc_layout(edgelist=edgelist, width=width, height=height)
        )

        nodes, nodes_set = self._prepare_nodes(
            nodelist=nodelist,
            pos=pos,
            node_params=node_params
        )

        links = self._prepare_edges(edgelist=edgelist, nodes_set=nodes_set)

        return nodes, links

    def _use_layout(self, position: Position):
        if self.layout is None:
            return position
        for node_name in position:
            matched = self.layout[self.layout["name"] == node_name]
            if not matched.empty:
                x = cast(float, matched["x"].item())
                y = cast(float, matched["y"].item())
                position[node_name] = [x, y]

        return position

    def _to_json(self, data):
        return json.dumps(data).encode('latin1').decode('utf-8')

    def _apply_settings(
        self,
        show_weights: bool = None,
        show_percents: bool = None,
        show_nodes_names: bool = None,
        show_all_edges_for_targets: bool = None,
        show_nodes_without_links: bool = None,
    ):
        settings = self.graph_settings.copy()
        if show_weights is not None:
            settings["show_weights"] = show_weights
        if show_percents is not None:
            settings["show_percents"] = show_percents
        if show_nodes_names is not None:
            settings["show_nodes_names"] = show_nodes_names
        if show_all_edges_for_targets is not None:
            settings["show_all_edges_for_targets"] = show_all_edges_for_targets
        if show_nodes_without_links is not None:
            settings["show_nodes_without_links"] = show_nodes_without_links
        return settings

    def _save_html(self, html: str):
        if self.env == "classic":
            # TODO смерджить пути нормально
            filename = 'graph_{}'.format(datetime.now()).replace(
                ':', '_').replace('.', '_') + '.html'
            path = self.rete.config["experiments_folder"] + "/" + filename
            with open(path, 'w', encoding="utf-8") as f:
                f.write(html)

    def save_html(self):
        self.plot_graph(display_graph=False)

    def plot_graph(
            self,
            display_graph: bool = True,
            targets: MutableMapping[str, str] = None,
            width: int = 960,
            height: int = 900,
            weight_template: str = None,
            show_weights: bool = None,
            show_percents: bool = None,
            show_nodes_names: bool = None,
            show_all_edges_for_targets: bool = None,
            show_nodes_without_links: bool = None,
            nodes_threshold: Threshold = None,
            links_threshold: Threshold = None,
    ):

        settings = self._apply_settings(
            show_weights=show_weights,
            show_percents=show_percents,
            show_nodes_names=show_nodes_names,
            show_all_edges_for_targets=show_all_edges_for_targets,
            show_nodes_without_links=show_nodes_without_links,
        )

        node_params = self._make_node_params(targets)

        norm_nodes_threshold = settings["nodes_threshold"] if "nodes_threshold" in settings else self._get_norm_node_threshold(
            nodes_threshold)
        norm_links_threshold = settings["links_threshold"] if "links_threshold" in settings else self._get_norm_link_threshold(
            links_threshold)
        cols = self.rete.get_nodelist_cols()

        nodes, links = self._make_template_data(
            node_params=node_params,
            width=width,
            height=height,
        )

        def to_js_val(val=None):
            return self._to_json(val) if val is not None else "undefined"

        def get_option(name: str):
            if name in settings:
                return self._to_json(settings[name])
            return "undefined"

        print("render graph3")

        init_graph_js = templates.__INIT_GRAPH__.format(
            server_id="'" + self.server.id + "'",
            env="'" + self.env + "'",
            links=self._to_json(links),
            node_params=self._to_json(node_params),
            nodes=self._to_json(nodes),
            layout_dump=1 if self.layout is not None else 0,
            links_weights_names=cols,
            node_cols_names=cols,
            show_weights=get_option("show_weights"),
            show_percents=get_option("show_percents"),
            show_nodes_names=get_option("show_nodes_names"),
            show_all_edges_for_targets=get_option(
                "show_all_edges_for_targets"),
            show_nodes_without_links=get_option("show_nodes_without_links"),
            nodes_threshold=to_js_val(norm_nodes_threshold),
            links_threshold=to_js_val(norm_links_threshold),
            weight_template="'" + weight_template +
            "'" if weight_template is not None else "undefined",
        )

        graph_styles = templates.__GRAPH_STYLES__.format()
        graph_body = templates.__GRAPH_BODY__.format()

        graph_script_src = "https://static.server.retentioneering.com/viztools/graph/rete-graph.js"

        init_graph_template = templates.__INIT_GRAPH__.format(
            server_id="'" + self.server.id + "'",
            env="'" + self.env + "'",
            node_params=self._to_json(node_params),
            links="<%= links %>",
            nodes="<%= nodes %>",
            layout_dump=1,
            links_weights_names=cols,
            node_cols_names=cols,
            show_weights="<%= show_weights %>",
            show_percents="<%= show_percents %>",
            show_nodes_names="<%= show_nodes_names %>",
            show_all_edges_for_targets="<%= show_all_edges_for_targets %>",
            show_nodes_without_links="<%= show_nodes_without_links %>",
            nodes_threshold="<%= nodes_threshold %>",
            links_threshold="<%= links_threshold %>",
            weight_template="undefined",
        )

        html_template = templates.__FULL_HTML__.format(
            content=templates.__RENDER_INNER_IFRAME__.format(
                id=generateId(),
                width=width,
                height=height,
                graph_body=graph_body,
                graph_styles=graph_styles,
                graph_script_src=graph_script_src,
                init_graph_js=init_graph_template,
                template=""
            ),
        )

        html = templates.__RENDER_INNER_IFRAME__.format(
            id=generateId(),
            width=width,
            height=height,
            graph_body=graph_body,
            graph_styles=graph_styles,
            graph_script_src=graph_script_src,
            init_graph_js=init_graph_js,
            template="",
        )

        full_html_page = templates.__FULL_HTML__.format(
            content=html,
        )
        self._save_html(full_html_page)

        if display_graph:
            display(HTML(html))
